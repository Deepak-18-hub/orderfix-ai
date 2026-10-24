"""Operator CLI and bounded, isolated SDK-local commerce demonstration."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
from unittest.mock import patch
from uuid import uuid4

from .runtime import CommerceConfigurationError, CommerceSettings, status_from_env
from .store import CommerceStore


class DemoFailed(RuntimeError):
    pass


async def _demo(output: Path, timeout: float) -> dict:
    """Run in its own process/event loop: Bureau shutdown cancels loop tasks.

    This exercises SDK typed queues/contexts and signed-only handlers. The SDK
    local dispatcher does not serialize or verify wire envelope signatures.
    """
    import httpx
    from uagents import Agent, Bureau, Model, Protocol
    from uagents.dispatch import dispatcher
    from uagents_core.contrib.protocols.chat import (
        ChatAcknowledgement, ChatMessage, MetadataContent, TextContent, chat_protocol_spec,
    )
    from uagents_core.registration import BatchRegistrationPolicy
    from uagents_core.envelope import Envelope
    from .engine import CommerceEngine
    from .payments import SimulatedGateway
    from .peers import MailService, PharmacyService, make_mail_protocol, make_pharmacy_protocol
    from .protocol import PeerResponse, make_peer_protocol
    from .ui import render_view

    class NoPublicRegistration(BatchRegistrationPolicy):
        def add_agent(self, agent_info, identity):
            pass

        async def register(self):
            raise DemoFailed("Unexpected public registration attempt in local demo.")

    class LocalAgent(Agent):
        async def _update_agent_status(self, active: bool):
            # SDK 0.26.1 reports startup status even with registration disabled.
            # This local-only subclass never updates a public identity.
            pass

    output.mkdir(parents=True, exist_ok=False)
    store = CommerceStore(str(output / "quickfix.sqlite3"))
    gateway = SimulatedGateway(output / "payments.sqlite3")
    pharmacy = PharmacyService(output / "pharmacy.sqlite3", auto_seed=True)
    mail = MailService(output / "mail.sqlite3")
    counts = Counter()
    operations = Counter()
    transcript = []
    selected = approved = completed = False
    failure = None
    agents = []
    final_view = {}
    wire_probe = {"valid_envelope_delivered": False, "tampered_envelope_rejected": False,
                  "transport": "http-loopback"}

    # A constructor-only hook suppresses the SDK's unconditional remote Almanac
    # version lookup. No contexts, handlers, message transports or results are mocked.
    with patch("uagents.agent.get_almanac_contract", return_value=None), patch("uagents.asgi.HOST", "127.0.0.1"):
        for name in ("quickfix-demo", "pharmacy-demo", "mail-demo", "customer-demo"):
            agents.append(LocalAgent(name=name, seed=uuid4().hex + uuid4().hex,
                network="testnet", mailbox=False, endpoint=None,
                enable_agent_inspector=False, publish_agent_details=False,
                store_message_history=False, mark_inactive_on_shutdown=False,
                report_events=False, shutdown_timeout=2, log_level=logging.ERROR))
        quickfix, med, post, customer = agents
        probe_received = asyncio.Event()

        class WireProbe(Model):
            marker: str

        @customer.on_message(WireProbe, allow_unverified=False)
        async def receive_probe(ctx, sender: str, message: WireProbe):
            if sender == quickfix.address and message.marker == "synthetic-signature-probe":
                counts["wire_probe_handler_deliveries"] += 1
                probe_received.set()

        async def verify_loopback_signatures():
            # Separate from the local-dispatch business flow: real HTTP enters
            # the SDK /submit verifier before the signed-only typed handler.
            async with asyncio.timeout(5):
                while bureau._server.server is None or not bureau._server.server.started:
                    await asyncio.sleep(0.01)
                socket = bureau._server.server.servers[0].sockets[0]
                port = socket.getsockname()[1]
                probe = WireProbe(marker="synthetic-signature-probe")
                envelope = Envelope(version=1, sender=quickfix.address, target=customer.address,
                    session=uuid4(), schema_digest=Model.build_schema_digest(WireProbe))
                envelope.encode_payload(probe.model_dump_json())
                envelope.sign(quickfix._identity)
                tampered = envelope.model_copy(deep=True)
                tampered.encode_payload(WireProbe(marker="tampered").model_dump_json())
                async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
                    rejected = await client.post(f"http://127.0.0.1:{port}/submit", json=tampered.model_dump(mode="json"))
                    valid = await client.post(f"http://127.0.0.1:{port}/submit", json=envelope.model_dump(mode="json"))
                await probe_received.wait()
                wire_probe["tampered_envelope_rejected"] = rejected.status_code == 400
                wire_probe["valid_envelope_delivered"] = valid.status_code == 200 and counts["wire_probe_handler_deliveries"] == 1
                if not all(wire_probe[key] for key in ("valid_envelope_delivered", "tampered_envelope_rejected")):
                    raise DemoFailed("Loopback signature verification probe failed.")
        engine = CommerceEngine(store, gateway, med.address, post.address,
                                pharmacy_mode="simulated", mail_mode="captured", auto_demo=True)
        med.include(make_pharmacy_protocol(pharmacy, quickfix.address), publish_manifest=False)
        post.include(make_mail_protocol(mail, quickfix.address), publish_manifest=False)

        async def received_peer(ctx, sender: str, response: PeerResponse):
            counts["peer_responses_received"] += 1
            await engine.receive(sender, response)

        quickfix.include(make_peer_protocol(received_peer, {med.address, post.address}), publish_manifest=False)
        quickfix_chat = Protocol(spec=chat_protocol_spec)
        customer_chat = Protocol(spec=chat_protocol_spec)
        views_by_message = {}

        @quickfix_chat.on_message(ChatMessage)
        async def chat_request(ctx, sender: str, message: ChatMessage):
            nonlocal failure
            if sender != customer.address:
                return
            counts["acp_requests_received"] += 1
            await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=message.msg_id))
            text = "\n".join(item.text for item in message.content if isinstance(item, TextContent))
            try:
                view = await engine.handle(sender, str(ctx.session), str(message.msg_id), text)
                rendered, card = render_view(view, view["language"])
                reply = ChatMessage(content=[TextContent(text=rendered)] + ([card] if card else []))
                views_by_message[str(reply.msg_id)] = view
                await ctx.send(sender, reply)
            except Exception as exc:
                failure = "Coordinator handler failed: " + type(exc).__name__
                await stop()

        @quickfix_chat.on_message(ChatAcknowledgement)
        async def chat_ack(ctx, sender: str, message: ChatAcknowledgement):
            if sender == customer.address:
                counts["acp_reply_acknowledgements"] += 1

        @customer_chat.on_message(ChatAcknowledgement)
        async def customer_ack(ctx, sender: str, message: ChatAcknowledgement):
            if sender == quickfix.address:
                counts["acp_request_acknowledgements"] += 1

        @customer_chat.on_message(ChatMessage)
        async def customer_reply(ctx, sender: str, message: ChatMessage):
            nonlocal selected, approved, completed, final_view, failure
            if sender != quickfix.address:
                return
            counts["acp_replies_received"] += 1
            await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=message.msg_id))
            view = views_by_message.pop(str(message.msg_id), {})
            transcript.append({"speaker": "quickfix", "text": "\n".join(
                block.text for block in message.content if isinstance(block, TextContent))})
            command = "status"
            for block in message.content:
                if not isinstance(block, MetadataContent):
                    continue
                kind = block.metadata.get("card_kind")
                payload = json.loads(block.metadata["card_payload"])
                if kind == "carousel" and not selected:
                    command = json.dumps(payload["items"][0]["primary_cta"]["selection"])
                    selected = True
                elif kind == "review" and not approved:
                    command = json.dumps(payload["approve_cta"]["selection"])
                    approved = True
            if (view.get("refund_state") == "processed"
                    and view.get("customer_notification") == "captured"
                    and view.get("pharmacy_notification") == "captured"):
                completed, final_view = True, view
                await stop()
                return
            if view.get("stage") in {"blocked", "rejected"}:
                failure = "Demo stopped at a blocked or rejected case."
                final_view = view
                await stop()
                return
            # Poll using real ACP messages in the same SDK conversation session.
            await asyncio.sleep(0.08)
            transcript.append({"speaker": "synthetic-customer", "text": command})
            await ctx.send(quickfix.address, ChatMessage(content=[TextContent(text=command)]))

        quickfix.include(quickfix_chat, publish_manifest=False)
        customer.include(customer_chat, publish_manifest=False)

        @quickfix.on_interval(period=0.05)
        async def worker(ctx):
            nonlocal failure
            try:
                await engine.advance()

                async def send(target, request):
                    counts["peer_requests_sent"] += 1
                    operations[request.operation] += 1
                    return await ctx.send(target, request)

                await engine.send_due(send)
            except Exception as exc:
                failure = "Worker failed: " + type(exc).__name__
                await stop()

        @customer.on_event("startup")
        async def begin(ctx):
            nonlocal failure
            try:
                await verify_loopback_signatures()
            except Exception as exc:
                failure = "Loopback signature probe failed: " + type(exc).__name__
                await stop()
                return
            transcript.append({"speaker": "synthetic-customer", "text": "QuickFix show my orders"})
            await ctx.send(quickfix.address, ChatMessage(content=[TextContent(text="QuickFix show my orders")]))

        bureau = Bureau(agents=agents, endpoint=None, registration_policy=NoPublicRegistration(),
                        network="testnet", shutdown_timeout=3, log_level=logging.ERROR)
        # SDK constructor treats port=0 as 8000; assign zero to its server so the
        # OS chooses an unused ephemeral port, on loopback only.
        bureau._server._port = 0

        async def stop():
            while bureau._server.server is None:
                await asyncio.sleep(0.01)
            bureau._server.server.should_exit = True

        async def deadline():
            nonlocal failure
            await asyncio.sleep(timeout)
            failure = "Local demo deadline exceeded."
            await stop()

        asyncio.create_task(deadline())
        try:
            await bureau.run_async()
        finally:
            for agent in agents:
                if dispatcher.contains(agent.address):
                    dispatcher.unregister(agent.address, agent)

    refund_count = gateway._db.execute("SELECT COUNT(*) FROM quickfix_sim_refunds").fetchone()[0]
    mail_count = mail._db.execute("SELECT COUNT(*) FROM peer_mail_captures").fetchone()[0]
    evidence = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "transport": "sdk-local-dispatch", "wire_signatures_verified": False,
        "separate_loopback_signature_probe": wire_probe,
        "public_registration_performed": False, "gateway_mode": "simulated",
        "pharmacy_mode": "simulated", "mail_mode": "captured",
        "real_funds_moved": False, "external_email_sent": False,
        "completed": completed and failure is None and refund_count == 1 and mail_count == 2,
        "counts": dict(counts), "operations": dict(operations),
        "simulated_refund_records": refund_count, "captured_email_records": mail_count,
        "final_state": {key: final_view.get(key) for key in (
            "stage", "order_state", "refund_state", "customer_notification", "pharmacy_notification")},
        "failure": failure, "transcript": transcript,
    }
    for resource in (store, gateway, pharmacy, mail):
        resource.close()
    (output / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence


def run_demo(output: str | Path, timeout: float = 30) -> dict:
    if not 2 <= timeout <= 60:
        raise DemoFailed("Demo deadline must be between 2 and 60 seconds.")
    return asyncio.run(_demo(Path(output), timeout))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QuickFix operator tools — test and simulation modes only")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Show credential-free readiness booleans")
    invite = sub.add_parser("invite", help="Explicit operator action: issue an account-link code")
    invite.add_argument("customer_ref")
    demo = sub.add_parser("demo", help="Run bounded SDK-local simulation; no funds or external emails")
    demo.add_argument("--output", default=None, help="New evidence directory (default: ignored data/ folder)")
    demo.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            print(json.dumps(status_from_env(), indent=2))
            return 0
        if args.command == "invite":
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", args.customer_ref):
                raise CommerceConfigurationError("Customer reference must be an opaque account ID.")
            settings = CommerceSettings.load()
            store = CommerceStore(settings.database)
            try:
                code = store.issue_invite(args.customer_ref)
            finally:
                store.close()
            print("Account-link code (expires in 2 hours; binds one conversation): " + code)
            return 0
        output = args.output or str(Path("data") / ("quickfix-demo-" + uuid4().hex[:12]))
        evidence = run_demo(output, args.timeout)
        print("SIMULATION: no real funds moved; emails captured locally, not sent.")
        probe = evidence["separate_loopback_signature_probe"]
        print("SDK local delivery; separate HTTP loopback signature probe passed: " + str(
            probe["valid_envelope_delivered"] and probe["tampered_envelope_rejected"]).lower())
        print("Evidence: " + str((Path(output) / "evidence.json").resolve()))
        print("Completed: " + str(evidence["completed"]).lower())
        return 0 if evidence["completed"] else 1
    except (CommerceConfigurationError, DemoFailed) as exc:
        print(str(exc))
        return 2
    except Exception:
        print("QuickFix command could not complete. Check local configuration and writable storage.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
