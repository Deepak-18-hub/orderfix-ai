"""Exercise the existing ACP router plus asynchronous same-session cards."""
import asyncio
import json
import logging
from types import SimpleNamespace
from uuid import uuid4

from uagents_core.contrib.protocols.chat import ChatAcknowledgement, ChatMessage, MetadataContent, TextContent

from orderfix.agent import make_protocol
from orderfix.commerce.acp import push_updates
from test_commerce_engine import Harness


class Context:
    def __init__(self):
        self._session = uuid4()
        self.sent = []
        self.logger = logging.getLogger("commerce-acp-test")
        self.agent = SimpleNamespace(address="agent1recipient")
        self.fail = False

    @property
    def session(self):
        return self._session

    async def send(self, target, message):
        if self.fail:
            return SimpleNamespace(status="failed")
        self.sent.append((target, self.session, message))
        return SimpleNamespace(status="delivered")


def test_existing_acp_router_pushes_order_review_and_outcome_without_polling(tmp_path):
    async def scenario():
        chat = Harness(tmp_path)
        ctx, worker = Context(), Context()
        worker_session = worker.session
        async def no_legacy(*args):
            raise AssertionError("Commerce flow reached legacy engine")
        protocol = make_protocol(SimpleNamespace(handle=no_legacy), commerce=chat.engine)
        handler = protocol._signed_message_handlers[ChatMessage.build_schema_digest(ChatMessage)]
        async def say(text):
            await handler(ctx, "alice", ChatMessage(content=[TextContent(text="@orderfix-ai " + text)]))
        async def receive_card():
            await chat.pump()
            await push_updates(chat.engine, worker)
            target, session, message = worker.sent[-1]
            assert target == "alice" and session == ctx.session
            assert worker.session == worker_session
            metadata = next(b.metadata for b in message.content if isinstance(b, MetadataContent))
            return json.loads(metadata["card_payload"])
        try:
            await say("quickfix")
            assert isinstance(ctx.sent[0][2], ChatAcknowledgement)
            orders = await receive_card()
            await say(json.dumps(orders["items"][0]["primary_cta"]["selection"]))
            review = await receive_card()
            assert review["approve_cta"]["selection"]["action"] == "qf_approve"
            await say(json.dumps(review["approve_cta"]["selection"]))
            status = await receive_card()
            assert "Refund processed and verified" in {r["value"] for r in status["summary_rows"]}
            assert chat.gateway.refund_calls == 1
            assert len(chat.captures()) == 2
            count = len(worker.sent)
            await push_updates(chat.engine, worker)
            assert len(worker.sent) == count  # unchanged state does not spam chat
        finally:
            chat.close()
    asyncio.run(scenario())


def test_background_delivery_failure_retries_without_financial_reexecution(tmp_path):
    async def scenario():
        chat, worker = Harness(tmp_path), Context()
        session = str(uuid4())
        try:
            await chat.engine.handle("alice", session, str(uuid4()), "quickfix")
            await chat.pump()
            worker.fail = True
            await push_updates(chat.engine, worker)
            assert worker.sent == []
            worker.fail = False
            await push_updates(chat.engine, worker)
            assert len(worker.sent) == 1
            assert str(worker.sent[0][1]) == session
            assert chat.gateway.refund_calls == 0
        finally:
            chat.close()
    asyncio.run(scenario())


def test_unrelated_orderfix_turn_still_uses_legacy_handler(tmp_path):
    async def scenario():
        chat, ctx = Harness(tmp_path), Context()
        calls = []
        async def legacy(*args):
            calls.append(args)
            return SimpleNamespace(text="Existing return request", language="en", review=None)
        try:
            protocol = make_protocol(SimpleNamespace(handle=legacy), commerce=chat.engine)
            handler = protocol._signed_message_handlers[ChatMessage.build_schema_digest(ChatMessage)]
            await handler(ctx, "alice", ChatMessage(content=[TextContent(text="return #1001 item 1 damaged")]))
            assert len(calls) == 1
            assert ctx.sent[-1][2].text() == "Existing return request"
        finally:
            chat.close()
    asyncio.run(scenario())
