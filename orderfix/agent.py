from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from uagents import Agent, Context, Model, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement, ChatMessage, ResourceContent, TextContent, chat_protocol_spec,
)

from .cards import review_card
from .config import Settings, make_engine
from .engine import Engine
from .locales import tr
from .profile import AVATAR_URL, DESCRIPTION, PUBLIC_HANDLE


class Health(Model):
    status: str = "ok"


def incoming_text(message: ChatMessage, address: str, handle: str | None = PUBLIC_HANDLE) -> str:
    """Remove only this recipient's leading ASI:One routing mention.

    Live direct chats include @agent-address or our registered public @handle in
    ACP text. Only exact configured recipients followed by whitespace may be
    removed. Do not strip prose, other recipients or embedded text.
    """
    text = "\n".join(block.text for block in message.content if isinstance(block, TextContent)).strip()
    for recipient in (address, handle):
        prefix = "@" + recipient if recipient else ""
        if prefix and text.startswith(prefix) and len(text) > len(prefix) and text[len(prefix)].isspace():
            return text[len(prefix):].lstrip()
    return text


def make_protocol(engine: Engine, cards: bool = True, commerce=None, require_real: bool = False) -> Protocol:
    protocol = Protocol(spec=chat_protocol_spec)

    @protocol.on_message(ChatMessage)
    async def on_message(ctx: Context, sender: str, message: ChatMessage):
        await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=message.msg_id))
        try:
            text = incoming_text(message, getattr(getattr(ctx, "agent", None), "address", ""))
            if commerce is not None and (require_real or commerce.handles(sender, str(ctx.session), text)):
                from .commerce.acp import message_for, record_delivery
                view = await commerce.handle(sender, str(ctx.session), str(message.msg_id), text)
                result = await ctx.send(sender, message_for(view, cards))
                if not getattr(commerce, "connected_waiting", False):
                    record_delivery(commerce, sender, ctx.session, view, result)
                return
            reply = await engine.handle(sender, str(ctx.session), str(message.msg_id), text)
            if any(isinstance(block, ResourceContent) for block in message.content):
                reply = reply.model_copy(update={"text": tr(reply.language, "image_text_only") + "\n" + reply.text})
            blocks = [TextContent(text=reply.text)]
            if cards and reply.review:
                blocks.append(review_card(reply.review, reply.language))
            result = await ctx.send(sender, ChatMessage(content=blocks))
            ctx.logger.info("ACP response send status: %s", getattr(result, "status", "unknown"))
        except Exception as exc:
            ctx.logger.error("ACP turn failed: %s", type(exc).__name__)
            await ctx.send(sender, ChatMessage(content=[TextContent(text=tr("en", "uncertain"))]))

    @protocol.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, message: ChatAcknowledgement):
        ctx.logger.info("ACP reply acknowledged: %s", message.acknowledged_msg_id)

    return protocol


def build_agent(settings: Settings | None = None) -> Agent:
    settings = settings or Settings.load()
    if len(settings.seed) < 32:
        raise ValueError("Run python -m orderfix.setup_local once to create a private stable AGENT_SEED")
    require_real = os.getenv("QUICKFIX_REQUIRE_REAL", "false").lower() == "true"
    # Real-only entry never constructs the legacy provider or seeds demo data.
    engine = None if require_real else make_engine(settings)
    commerce = None
    if require_real:
        from .commerce.runtime import make_connected_engine
        commerce = make_connected_engine()
    elif os.getenv("QUICKFIX_ENABLED", "false").lower() == "true":
        from .commerce.runtime import make_engine_from_env
        commerce = make_engine_from_env()
    agent = Agent(name="OrderFix", seed=settings.seed, port=settings.port, mailbox=True,
        publish_agent_details=True, readme_path=str(Path(__file__).resolve().parents[1] / "AGENT_PROFILE.md"),
        description=DESCRIPTION, avatar_url=AVATAR_URL,
        store_message_history=False, enable_agent_inspector=False)
    agent.include(make_protocol(engine, settings.cards, commerce, require_real), publish_manifest=True)
    if commerce is not None and not getattr(commerce, "connected_waiting", False):
        from .commerce.acp import attach_worker
        attach_worker(agent, commerce, settings.cards)

    # uAgents 0.26.1's mailbox run() launches its poller and returns. With no
    # REST handlers, Agent.run_async() drops its server task and immediately
    # shuts down the poller. A minimal health route keeps the supported server
    # lifecycle alive without enabling inspector or registration controls.
    @agent.on_rest_get("/health", Health)
    async def health(_ctx: Context):
        return Health()

    @agent.on_event("startup")
    async def startup(ctx: Context):
        store_mode = ("merchant connection pending" if getattr(commerce, "connected_waiting", False)
                      else "connected merchant" if require_real else settings.mode)
        ctx.logger.info("OrderFix address: %s | ACP: %s | store: %s", agent.address, chat_protocol_spec.version, store_mode)
        ctx.logger.info("Mailbox mode: register once with python -m orderfix.register --register. Keep this process running; no public tunnel is required.")

    @agent.on_event("shutdown")
    async def shutdown(ctx: Context):
        if engine is not None:
            engine.provider.close()
            engine.storage.close()
        if commerce is not None and not getattr(commerce, "connected_waiting", False):
            commerce.gateway.close()
            commerce.store.close()

    return agent


def main():
    # uAgents 0.26.1 reads readme_path using the platform default encoding.
    # Windows must enable UTF-8 before loading our Hindi/Kannada profile.
    if os.name == "nt" and not sys.flags.utf8_mode:
        os.execv(sys.executable, [sys.executable, "-X", "utf8", "-m", "orderfix.agent"])
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    build_agent().run()


if __name__ == "__main__":
    main()
