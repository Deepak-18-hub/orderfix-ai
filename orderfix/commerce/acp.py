"""Background ACP updates retain the customer's original conversation session."""
from __future__ import annotations

from copy import copy
import hashlib
import json
from uuid import UUID

from uagents_core.contrib.protocols.chat import ChatMessage, TextContent
from uagents_core.types import DeliveryStatus

from orderfix.storage import owner_key
from .protocol import make_peer_protocol
from .ui import render_view


def fingerprint(view):
    return hashlib.sha256(json.dumps(view, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def message_for(view, cards=True):
    text, card = render_view(view, view.get("language", "en"))
    return ChatMessage(content=[TextContent(text=text)] + ([card] if cards and card else []))


def record_delivery(engine, sender, session_id, view, result):
    if getattr(result, "status", None) in (DeliveryStatus.DELIVERED, "delivered"):
        engine.store.mark_delivered(owner_key(sender, str(session_id)), fingerprint(view))


async def push_updates(engine, ctx, cards=True):
    for owner, session in engine.store.sessions():
        if not session.route_sender or not session.route_session:
            continue
        view = engine._view(owner)
        digest = fingerprint(view)
        if engine.store.delivered(owner, digest):
            continue
        try:
            # uAgents 0.26.1 proactive Context.send has no session argument.
            # Copy the interval context so concurrent sends keep their own
            # sessions; do not mutate the shared worker context.
            routed = copy(ctx)
            routed._session = UUID(session.route_session)
            result = await routed.send(session.route_sender, message_for(view, cards))
            if getattr(result, "status", None) in (DeliveryStatus.DELIVERED, "delivered"):
                engine.store.mark_delivered(owner, digest)
        except Exception as exc:
            ctx.logger.warning("QuickFix update delivery pending: %s", type(exc).__name__)


def attach_worker(agent, engine, cards=True):
    async def received(_ctx, sender, response):
        await engine.receive(sender, response)

    agent.include(make_peer_protocol(received, {engine.med_agent, engine.mail_agent}), publish_manifest=True)

    @agent.on_interval(period=2)
    async def work(ctx):
        try:
            await engine.send_due(ctx.send)
            await engine.advance()
            await push_updates(engine, ctx, cards)
        except Exception as exc:
            ctx.logger.error("QuickFix worker will retry: %s", type(exc).__name__)
