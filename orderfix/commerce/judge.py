"""Laptop judging launcher: registered ACP coordinator plus local simulators.

Only the existing coordinator uses Agentverse/mailbox registration. No payment
gateway or mail sender is enabled. This is not hosted deployment.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import hmac
import logging
import os
from pathlib import Path
import sys

from uagents import Agent, Bureau
from uagents.dispatch import dispatcher
from uagents_core.registration import BatchRegistrationPolicy

from orderfix.agent import build_agent
from orderfix.config import Settings
from .peers import MailService, PharmacyService, make_mail_protocol, make_pharmacy_protocol


class LocalPeer(Agent):
    async def _update_agent_status(self, active: bool):
        """A local simulator must never publish its presence to Agentverse."""


class NoBatchRegistration(BatchRegistrationPolicy):
    def add_agent(self, agent_info, identity):
        """Coordinator retains its individual policy; peers stay local."""

    async def register(self):
        """The Bureau must not register local simulator identities."""


def _peer_seed(coordinator_seed: str, role: str) -> str:
    return hmac.new(coordinator_seed.encode("utf-8"),
                    f"orderfix:quickfix-judge:{role}:v1".encode("ascii"), hashlib.sha256).hexdigest()


@contextmanager
def _temporary_environment(values):
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@dataclass
class JudgeRuntime:
    bureau: Bureau
    coordinator: Agent
    pharmacy_agent: Agent
    mail_agent: Agent
    pharmacy: PharmacyService
    mail: MailService
    data_dir: Path

    def run(self):
        # The mailbox supplies public ingress. The local lifecycle/health server
        # does not need to listen on LAN interfaces. SDK 0.26.1 uses this constant.
        import uagents.asgi as asgi
        old_host = asgi.HOST
        asgi.HOST = "127.0.0.1"
        try:
            self.bureau.run()
        finally:
            asgi.HOST = old_host

    async def close_unstarted(self):
        """Release construction-only instances; running Bureau owns shutdown."""
        agents = (self.coordinator, self.pharmacy_agent, self.mail_agent)
        if any(agent._message_queue_task is not None for agent in agents):
            raise RuntimeError("A started judging runtime must shut down through its Bureau.")
        for agent in agents:
            await agent.run_shutdown_tasks()
            if dispatcher.contains(agent.address):
                dispatcher.unregister(agent.address, agent)


def build_judge(settings: Settings | None = None,
                data_dir: str | Path = "data/quickfix-judge") -> JudgeRuntime:
    """Preserve the registered seed while forcing all business services local.

    Construction may perform the SDK's ordinary ledger compatibility lookup and
    schedule coordinator manifests. It does not run the mailbox or Bureau.
    """
    settings = settings or Settings.load()
    if not isinstance(settings.seed, str) or len(settings.seed) < 32:
        raise ValueError("The existing private AGENT_SEED is required; do not create a new identity for judging.")
    root = Path(data_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    pharmacy = PharmacyService(root / "pharmacy.sqlite3", auto_seed=True)
    mail = MailService(root / "mail.sqlite3")
    peer_options = dict(network="testnet", mailbox=False, endpoint=None,
                        enable_agent_inspector=False, publish_agent_details=False,
                        store_message_history=False, report_events=False,
                        mark_inactive_on_shutdown=False, shutdown_timeout=5)
    med_agent = LocalPeer(name="QuickFix local pharmacy",
                         seed=_peer_seed(settings.seed, "pharmacy"), **peer_options)
    mail_agent = LocalPeer(name="QuickFix local mail",
                          seed=_peer_seed(settings.seed, "mail"), **peer_options)

    # Settings passed to the existing bridge also disable its legacy external
    # store and optional LLM calls. Neither this scope nor the launcher writes .env.
    local_settings = replace(settings, mode="demo", database=str(root / "legacy-demo.sqlite3"), asi_key="")
    overrides = {
        "QUICKFIX_ENABLED": "true", "QUICKFIX_DB": str(root / "commerce.sqlite3"),
        "QUICKFIX_GATEWAY": "simulated", "QUICKFIX_PHARMACY_MODE": "simulated",
        "QUICKFIX_MAIL_MODE": "captured", "QUICKFIX_AUTO_DEMO": "true",
        "QUICKFIX_MERCHANT_REF": "demo-pharmacy",
        "MEDGUARDIAN_AGENT_ADDRESS": med_agent.address, "MAIL_AGENT_ADDRESS": mail_agent.address,
        "RAZORPAY_KEY_ID": "", "RAZORPAY_KEY_SECRET": "",
    }
    try:
        with _temporary_environment(overrides):
            coordinator = build_agent(local_settings)
    except Exception:
        pharmacy.close()
        mail.close()
        for peer in (med_agent, mail_agent):
            if dispatcher.contains(peer.address):
                dispatcher.unregister(peer.address, peer)
        raise
    med_agent.include(make_pharmacy_protocol(pharmacy, coordinator.address), publish_manifest=False)
    mail_agent.include(make_mail_protocol(mail, coordinator.address), publish_manifest=False)

    @med_agent.on_event("shutdown")
    async def close_pharmacy(_ctx):
        pharmacy.close()

    @mail_agent.on_event("shutdown")
    async def close_mail(_ctx):
        mail.close()

    coordinator_policy = coordinator._registration_policy
    coordinator_endpoints = list(coordinator._endpoints)
    bureau = Bureau(port=settings.port, endpoint=None, network="testnet",
                    registration_policy=NoBatchRegistration(), shutdown_timeout=15)
    for agent in (coordinator, med_agent, mail_agent):
        bureau.add(agent)
    # Bureau.add clears individual policies. Restore ONLY the existing mailbox
    # coordinator's standard policy, preserving registration renewal behavior.
    coordinator.update_registration_policy(coordinator_policy)
    if coordinator._endpoints != coordinator_endpoints or coordinator.mailbox_client is None:
        raise RuntimeError("The SDK did not preserve the registered mailbox configuration.")
    if med_agent._registration_policy is not None or mail_agent._registration_policy is not None:
        raise RuntimeError("Local peers unexpectedly acquired a registration policy.")
    return JudgeRuntime(bureau, coordinator, med_agent, mail_agent, pharmacy, mail, root)


def main(argv: list[str] | None = None) -> int:
    if os.name == "nt" and not sys.flags.utf8_mode and argv is None:
        os.execv(sys.executable, [sys.executable, "-X", "utf8", "-m", "orderfix.commerce.judge", *sys.argv[1:]])
    parser = argparse.ArgumentParser(description="Keep the registered OrderFix agent online with local judging simulators")
    parser.add_argument("--data-dir", default="data/quickfix-judge", help="Persistent local simulation data directory")
    parser.add_argument("--port", type=int, default=None, help="Local loopback health-server port")
    args = parser.parse_args(argv)
    try:
        settings = Settings.load()
        if args.port is not None:
            if not 1 <= args.port <= 65535:
                raise ValueError("Port must be between 1 and 65535.")
            settings = replace(settings, port=args.port)
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        runtime = build_judge(settings, args.data_dir)
        print("Judging simulation: no real payments, pharmacy transactions or external email.")
        print("Coordinator address: " + runtime.coordinator.address)
        print("Keep this process and laptop online. The existing Agentverse mailbox supplies access.")
        runtime.run()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("Judging launcher could not start. Check the existing seed, writable data directory and available local port.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
