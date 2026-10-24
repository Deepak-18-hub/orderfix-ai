"""A real-data runtime must not fall through to legacy synthetic order tools."""
import asyncio
from types import SimpleNamespace

from uagents_core.contrib.protocols.chat import ChatMessage, TextContent

from orderfix.agent import make_protocol
from orderfix.commerce.runtime import WaitingCommerce
from test_commerce_acp import Context


def test_waiting_merchant_answers_old_demo_commands_without_fake_orders():
    async def scenario():
        async def forbidden(*args):
            raise AssertionError("Legacy demo provider was reached")
        waiting, ctx = WaitingCommerce(), Context()
        protocol = make_protocol(SimpleNamespace(handle=forbidden), commerce=waiting, require_real=True)
        handler = protocol._signed_message_handlers[ChatMessage.build_schema_digest(ChatMessage)]
        for text in ("quickfix show my orders", "return #1001 item 1 damaged", "confirm", "order #1001"):
            await handler(ctx, "alice", ChatMessage(content=[TextContent(text=text)]))
            result = ctx.sent[-1][2].text()
            assert "cannot retrieve real orders" in result.lower()
            assert "349" not in result and "Willow" not in result
        assert not hasattr(waiting, "store")
    asyncio.run(scenario())


def test_real_only_routing_does_not_depend_on_keyword_detection():
    async def scenario():
        called = []
        waiting = WaitingCommerce()
        async def real_handle(*args):
            called.append(args[-1])
            return await waiting.handle(*args)
        async def forbidden(*args):
            raise AssertionError("A real-only turn reached the simulator")
        real = SimpleNamespace(handles=lambda *args: False, handle=real_handle, connected_waiting=True)
        protocol = make_protocol(SimpleNamespace(handle=forbidden), commerce=real, require_real=True)
        ctx = Context()
        await protocol._signed_message_handlers[ChatMessage.build_schema_digest(ChatMessage)](
            ctx, "alice", ChatMessage(content=[TextContent(text="hello")]))
        assert called == ["hello"]
    asyncio.run(scenario())


def test_real_agent_does_not_construct_or_seed_legacy_provider(monkeypatch):
    from orderfix import agent
    from orderfix.commerce import runtime
    from orderfix.config import Settings
    monkeypatch.setenv("QUICKFIX_REQUIRE_REAL", "true")
    monkeypatch.setattr(runtime, "make_connected_engine", lambda: WaitingCommerce())
    def forbidden(*args):
        raise AssertionError("Legacy provider must not even be constructed")
    monkeypatch.setattr(agent, "make_engine", forbidden)
    class LocalAgent:
        def __init__(self, **kwargs):
            self.address = "existing-identity"
        def include(self, *args, **kwargs):
            pass
        def on_rest_get(self, *args):
            return lambda handler: handler
        def on_event(self, *args):
            return lambda handler: handler
    monkeypatch.setattr(agent, "Agent", LocalAgent)
    assert agent.build_agent(Settings(seed="existing-stable-private-seed-for-test-only")).address == "existing-identity"
