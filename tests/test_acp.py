import asyncio
import json
import logging
import pytest
from types import SimpleNamespace
from uuid import uuid4

from uagents_core.contrib.protocols.chat import ChatAcknowledgement, ChatMessage, MetadataContent, TextContent, chat_protocol_spec

from orderfix.agent import incoming_text, make_protocol
from orderfix.demo_store import DemoProvider
from orderfix.engine import Engine, approval
from orderfix.storage import Storage


class ContextStub:
    def __init__(self):
        self.session = uuid4()
        self.logger = logging.getLogger("test-acp")
        self.sent = []
        self.agent = SimpleNamespace(address="agent1test-recipient")

    async def send(self, target, message):
        self.sent.append((target, message))
        return SimpleNamespace(status="delivered")


@pytest.mark.parametrize("prefix", ["", "@agent1test-recipient ", "@orderfix-ai "])
@pytest.mark.parametrize("confirmation", ["selection", "live_card", "manual"])
def test_official_acp_review_and_confirmation(tmp_path, prefix, confirmation):
    async def scenario():
        path = str(tmp_path / "acp.sqlite3")
        engine = Engine(Storage(path), DemoProvider(path))
        protocol = make_protocol(engine)
        ctx = ContextStub()
        handler = protocol._signed_message_handlers[ChatMessage.build_schema_digest(ChatMessage)]
        msg = ChatMessage(content=[TextContent(text=prefix + "return #1001 item 1 damaged")])
        await handler(ctx, "test-customer", msg)
        ack, response = ctx.sent[0][1], ctx.sent[1][1]
        assert isinstance(ack, ChatAcknowledgement)
        assert ack.acknowledged_msg_id == msg.msg_id
        assert isinstance(response, ChatMessage)
        card = next(c for c in response.content if isinstance(c, MetadataContent))
        assert card.metadata["card_protocol_version"] == "1"
        assert card.metadata["requires_card_interaction"] == "true"
        payload = json.loads(card.metadata["card_payload"])
        if confirmation == "live_card":
            payload["approve_cta"]["selection"]["approved"] = True
        selection = payload["approve_cta"]["selection"]
        command = f"confirm {selection['review_token']}" if confirmation == "manual" else json.dumps(selection)
        await handler(ctx, "test-customer", ChatMessage(content=[TextContent(text=prefix + command)]))
        assert "saved and verified" in ctx.sent[-1][1].text()
        assert all(block.type != "end-session" for block in response.content)
        assert protocol.manifest()["metadata"]["version"] == chat_protocol_spec.version == "0.3.0"
        engine.provider.close()
        engine.storage.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("action,decision,expected", [
    ("confirm", True, ("confirm", "AABBCCDDEEFF")),
    ("cancel", False, ("cancel", "AABBCCDDEEFF")),
    ("confirm", False, None),
    ("cancel", True, None),
    ("confirm", "true", None),
    ("confirm", 1, None),
])
def test_live_card_approval_flag_must_match_the_explicit_action(action, decision, expected):
    assert approval(json.dumps({"action": action, "review_token": "AABBCCDDEEFF", "approved": decision})) == expected


@pytest.mark.parametrize("source,expected", [
    ("@agent1test-recipient confirm AABBCCDDEEFF", "confirm AABBCCDDEEFF"),
    ("@orderfix-ai confirm AABBCCDDEEFF", "confirm AABBCCDDEEFF"),
    ("@orderfix-ai\tconfirm 00BBCCDDEEFF", "confirm 00BBCCDDEEFF"),
    ("@another-agent confirm AABBCCDDEEFF", "@another-agent confirm AABBCCDDEEFF"),
    ("do not @agent1test-recipient confirm AABBCCDDEEFF", "do not @agent1test-recipient confirm AABBCCDDEEFF"),
    ("do not @orderfix-ai confirm AABBCCDDEEFF", "do not @orderfix-ai confirm AABBCCDDEEFF"),
    ("@agent1test-recipient-extra confirm AABBCCDDEEFF", "@agent1test-recipient-extra confirm AABBCCDDEEFF"),
    ("@orderfix-ai-extra confirm AABBCCDDEEFF", "@orderfix-ai-extra confirm AABBCCDDEEFF"),
    ("@orderfix-ai.confirm AABBCCDDEEFF", "@orderfix-ai.confirm AABBCCDDEEFF"),
    ("@orderfix-aiconfirm AABBCCDDEEFF", "@orderfix-aiconfirm AABBCCDDEEFF"),
    ("@orderfix-ai", "@orderfix-ai"),
])
def test_routing_mention_is_not_general_consent_extraction(source, expected):
    assert incoming_text(ChatMessage(content=[TextContent(text=source)]), "agent1test-recipient") == expected


@pytest.mark.parametrize("source,handle,expected", [
    ("@orderfix-ai confirm 00BBCCDDEEFF", "orderfix-ai", "confirm 00BBCCDDEEFF"),
    ("@ confirm AABBCCDDEEFF", "orderfix-ai", "@ confirm AABBCCDDEEFF"),
    ("@my-fork confirm AABBCCDDEEFF", "my-fork", "confirm AABBCCDDEEFF"),
    ("@orderfix-ai confirm AABBCCDDEEFF", "my-fork", "@orderfix-ai confirm AABBCCDDEEFF"),
    ("@orderfix-ai confirm AABBCCDDEEFF", None, "@orderfix-ai confirm AABBCCDDEEFF"),
])
def test_configured_handle_with_no_transport_address(source, handle, expected):
    assert incoming_text(ChatMessage(content=[TextContent(text=source)]), "", handle) == expected


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
