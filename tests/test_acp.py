import asyncio
import json
import logging
from types import SimpleNamespace
from uuid import uuid4

from uagents_core.contrib.protocols.chat import ChatAcknowledgement, ChatMessage, MetadataContent, TextContent, chat_protocol_spec

from orderfix.agent import make_protocol
from orderfix.demo_store import DemoProvider
from orderfix.engine import Engine
from orderfix.storage import Storage


class ContextStub:
    def __init__(self):
        self.session = uuid4()
        self.logger = logging.getLogger("test-acp")
        self.sent = []

    async def send(self, target, message):
        self.sent.append((target, message))
        return SimpleNamespace(status="delivered")


def test_official_acp_review_and_confirmation(tmp_path):
    async def scenario():
        path = str(tmp_path / "acp.sqlite3")
        engine = Engine(Storage(path), DemoProvider(path))
        protocol = make_protocol(engine)
        ctx = ContextStub()
        handler = protocol._signed_message_handlers[ChatMessage.build_schema_digest(ChatMessage)]
        msg = ChatMessage(content=[TextContent(text="return #1001 item 1 damaged")])
        await handler(ctx, "test-customer", msg)
        ack, response = ctx.sent[0][1], ctx.sent[1][1]
        assert isinstance(ack, ChatAcknowledgement)
        assert ack.acknowledged_msg_id == msg.msg_id
        assert isinstance(response, ChatMessage)
        card = next(c for c in response.content if isinstance(c, MetadataContent))
        assert card.metadata["card_protocol_version"] == "1"
        assert card.metadata["requires_card_interaction"] == "true"
        payload = json.loads(card.metadata["card_payload"])
        await handler(ctx, "test-customer", ChatMessage(content=[TextContent(text=json.dumps(payload["approve_cta"]["selection"]))]))
        assert "saved and verified" in ctx.sent[-1][1].text()
        assert all(block.type != "end-session" for block in response.content)
        assert protocol.manifest()["metadata"]["version"] == chat_protocol_spec.version == "0.3.0"
        engine.provider.close()
        engine.storage.close()
    asyncio.run(scenario())


def test_acknowledgements_do_not_trigger_reply_loop(tmp_path):
    async def scenario():
        path = str(tmp_path / "ack.sqlite3")
        engine = Engine(Storage(path), DemoProvider(path))
        protocol = make_protocol(engine)
        ctx = ContextStub()
        await protocol._signed_message_handlers[ChatAcknowledgement.build_schema_digest(ChatAcknowledgement)](
            ctx, "sender", ChatAcknowledgement(acknowledged_msg_id=uuid4()))
        assert ctx.sent == []
        engine.provider.close()
        engine.storage.close()
    asyncio.run(scenario())
