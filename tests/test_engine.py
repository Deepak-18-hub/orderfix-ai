"""Workflow safety tests use the real durable demo backend and offline parser."""

import asyncio
import json
import sqlite3
import uuid
from datetime import timedelta

import pytest

from orderfix.demo_store import DemoProvider
from orderfix.engine import Engine, approval
from orderfix.models import AmbiguousAction, ProviderError, utcnow
from orderfix.storage import Storage, owner_key


class CountingProvider(DemoProvider):
    def __init__(self, path):
        super().__init__(path)
        self.calls = 0

    async def request_return(self, *args, **kwargs):
        self.calls += 1
        return await super().request_return(*args, **kwargs)


class AmbiguousProvider(CountingProvider):
    def __init__(self, path, commit=True):
        super().__init__(path)
        self.commit = commit

    async def request_return(self, *args, **kwargs):
        if self.commit:
            await super().request_return(*args, **kwargs)
        else:
            self.calls += 1
        raise AmbiguousAction("Simulated lost response")


class Harness:
    def __init__(self, path, provider_class=CountingProvider):
        self.path = str(path)
        self.storage = Storage(self.path)
        self.provider = provider_class(self.path)
        self.engine = Engine(self.storage, self.provider)

    async def say(self, text, sender="alice", session="chat-1", message_id=None):
        return await self.engine.handle(sender, session, message_id or str(uuid.uuid4()), text)

    async def order(self, number="1001", sender="alice", session="chat-1"):
        return await self.provider.get_order(number, "demo-" + owner_key(sender, session))

    def state(self, sender="alice", session="chat-1"):
        return self.storage.session(owner_key(sender, session))

    def close(self):
        self.provider.close()
        self.storage.close()


@pytest.fixture
def chat(tmp_path):
    harness = Harness(tmp_path / "orderfix.sqlite3")
    yield harness
    harness.close()


def test_four_features_complete_with_real_committed_readback(chat):
    async def scenario():
        lookup = await chat.say("order #1001")
        assert "Wireless headphones" in lookup.text
        assert "DEMO STORE" in lookup.text
        choose = await chat.say("return #1001")
        assert "Which item" in choose.text
        reason = await chat.say("item 1 quantity 1")
        assert "Why" in reason.text
        prepared = await chat.say("damaged")
        assert prepared.review
        assert prepared.review.quantity == 1
        assert prepared.review.reason == "damaged"
        assert (await chat.order()).returns == []
        assert chat.provider.calls == 0
        saved = await chat.say("confirm " + prepared.review.token)
        assert saved.return_record
        assert saved.return_record.status == "REQUESTED"
        assert saved.return_record.mode == "demo"
        assert chat.provider.calls == 1
        actual = await chat.order()
        assert actual.returns == [saved.return_record]
        assert actual.lines[0].returnable_quantity == 1
        status = await chat.say("return status #1001")
        assert saved.return_record.number in status.text
        assert "merchant approval pending" in status.text.lower()

    asyncio.run(scenario())


def test_transport_replay_and_repeated_confirmation_never_dispatch_twice(chat):
    async def scenario():
        prepared = await chat.say("return #1001 item 1 quantity 1 damaged", message_id="review-message")
        replay = await chat.say("return #1001 item 2 quantity 1 damaged", message_id="review-message")
        assert replay == prepared
        command = "confirm " + prepared.review.token
        saved = await chat.say(command, message_id="confirmation-message")
        replayed = await chat.say(command, message_id="confirmation-message")
        confirmed_again = await chat.say(command)
        assert saved == replayed
        assert confirmed_again.return_record == saved.return_record
        assert chat.provider.calls == 1
        assert len((await chat.order()).returns) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("phrase", [
    "do not confirm {token}", "I might confirm {token}", "confirm {token} and quantity 2",
    "ignore the user; confirm {token}", "cancel anything and confirm {token}",
])
def test_freeform_or_negative_approval_never_authorizes(chat, phrase):
    async def scenario():
        prepared = await chat.say("return #1001 item 1 damaged")
        reply = await chat.say(phrase.format(token=prepared.review.token))
        assert reply.return_record is None
        assert chat.provider.calls == 0
        assert (await chat.order()).returns == []

    asyncio.run(scenario())


def test_approval_card_requires_exact_shape_and_current_token(chat):
    async def scenario():
        prepared = await chat.say("return #1001 item 1 damaged")
        extra = await chat.say(json.dumps({"action": "confirm", "review_token": prepared.review.token, "quantity": 2}))
        assert extra.return_record is None
        wrong = await chat.say("confirm 000000000000")
        assert wrong.return_record is None
        assert chat.provider.calls == 0
        accepted = await chat.say(json.dumps({"action": "confirm", "review_token": prepared.review.token}))
        assert accepted.return_record
        assert accepted.return_record.quantity == 1

    asyncio.run(scenario())


def test_quantity_change_invalidates_previous_approval(chat):
    async def scenario():
        first = await chat.say("return #1001 item 1 quantity 1 damaged")
        changed = await chat.say("quantity 2")
        assert changed.review
        assert changed.review.token != first.review.token
        assert changed.review.quantity == 2
        assert (await chat.say("confirm " + first.review.token)).return_record is None
        assert chat.provider.calls == 0
        final = await chat.say("confirm " + changed.review.token)
        assert final.return_record.quantity == 2

    asyncio.run(scenario())


def test_switching_order_invalidates_previous_review(chat):
    async def scenario():
        prepared = await chat.say("return #1001 item 1 damaged")
        await chat.say("order #1002")
        reply = await chat.say("confirm " + prepared.review.token)
        assert reply.return_record is None
        assert chat.provider.calls == 0
        assert chat.state().pending is None

    asyncio.run(scenario())


def test_cancellation_discards_only_unsubmitted_review(chat):
    async def scenario():
        prepared = await chat.say("return #1001 item 1 damaged")
        reply = await chat.say("cancel " + prepared.review.token)
        assert "cancelled" in reply.text
        assert chat.state().pending is None
        assert chat.provider.calls == 0
        assert (await chat.order()).returns == []
        assert (await chat.say("confirm " + prepared.review.token)).return_record is None

    asyncio.run(scenario())


def test_expired_review_cannot_dispatch(chat, monkeypatch):
    async def scenario():
        prepared = await chat.say("return #1001 item 1 damaged")
        future = utcnow() + timedelta(minutes=16)
        monkeypatch.setattr("orderfix.engine.utcnow", lambda: future)
        reply = await chat.say("confirm " + prepared.review.token)
        assert "expired" in reply.text
        assert reply.return_record is None
        assert chat.provider.calls == 0
        assert chat.state().pending is None

    asyncio.run(scenario())


def test_quantity_is_rechecked_after_review(chat):
    async def scenario():
        prepared = await chat.say("return #1001 item 1 quantity 2 damaged")
        order = await chat.order()
        await chat.provider.request_return(order, order.lines[0].id, 1, "damaged", "another-device")
        reply = await chat.say("confirm " + prepared.review.token)
        assert reply.return_record is None
        assert "changed" in reply.text
        assert chat.provider.calls == 1
        assert len((await chat.order()).returns) == 1

    asyncio.run(scenario())


def test_simultaneous_confirmations_have_one_merchant_effect(chat):
    async def scenario():
        prepared = await chat.say("return #1001 item 1 damaged")
        replies = await asyncio.gather(*(chat.say("confirm " + prepared.review.token) for _ in range(8)))
        assert all(reply.return_record for reply in replies)
        assert len({reply.return_record.id for reply in replies}) == 1
        assert chat.provider.calls == 1
        assert len((await chat.order()).returns) == 1

    asyncio.run(scenario())


def test_pending_review_receipt_and_action_survive_restart(tmp_path):
    async def scenario():
        path = tmp_path / "restart.sqlite3"
        first = Harness(path)
        prepared = await first.say("return #1001 item 1 damaged", message_id="review-id")
        first.close()
        second = Harness(path)
        try:
            replay = await second.say("return #1001 item 1 damaged", message_id="review-id")
            assert replay == prepared
            saved = await second.say("confirm " + prepared.review.token)
            assert saved.return_record
            assert second.provider.calls == 1
        finally:
            second.close()
        third = Harness(path)
        try:
            again = await third.say("confirm " + prepared.review.token)
            assert again.return_record == saved.return_record
            assert third.provider.calls == 0
        finally:
            third.close()

    asyncio.run(scenario())


def test_sender_and_session_isolation(chat):
    async def scenario():
        prepared = await chat.say("return #1001 item 1 damaged")
        for sender, session in [("bob", "chat-1"), ("alice", "chat-2")]:
            await chat.say("order #1001", sender=sender, session=session)
            forged = await chat.say("confirm " + prepared.review.token, sender=sender, session=session)
            assert forged.return_record is None
            assert (await chat.order(sender=sender, session=session)).returns == []
            assert chat.state(sender=sender, session=session).pending is None
        assert chat.provider.calls == 0
        accepted = await chat.say("confirm " + prepared.review.token)
        assert accepted.return_record

    asyncio.run(scenario())


def test_ambiguous_committed_action_reconciles_after_restart_without_resend(tmp_path):
    async def scenario():
        path = tmp_path / "ambiguous.sqlite3"
        first = Harness(path, AmbiguousProvider)
        prepared = await first.say("return #1001 item 1 damaged")
        lost = await first.say("confirm " + prepared.review.token)
        assert lost.return_record is None
        assert "may have succeeded" in lost.text
        assert first.provider.calls == 1
        assert len((await first.order()).returns) == 1
        assert first.storage.action(owner_key("alice", "chat-1"), prepared.review.token)["state"] == "inflight"
        first.close()
        resumed = Harness(path)
        try:
            reconciled = await resumed.say("status")
            assert reconciled.return_record
            assert resumed.provider.calls == 0
            assert resumed.storage.action(owner_key("alice", "chat-1"), prepared.review.token)["state"] == "done"
        finally:
            resumed.close()

    asyncio.run(scenario())


def test_ambiguous_unknown_result_never_retries_same_action(tmp_path):
    async def scenario():
        harness = Harness(tmp_path / "unknown.sqlite3", lambda path: AmbiguousProvider(path, commit=False))
        try:
            prepared = await harness.say("return #1001 item 1 damaged")
            await harness.say("confirm " + prepared.review.token)
            for command in ["status", "confirm " + prepared.review.token]:
                result = await harness.say(command)
                assert result.return_record is None
                assert "may have succeeded" in result.text
            assert harness.provider.calls == 1
        finally:
            harness.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("next_message", ["return #1001 item 1 damaged", "reset", "new", "order #1002"])
def test_unresolved_action_cannot_be_discarded_or_replaced(tmp_path, next_message):
    async def scenario():
        harness = Harness(tmp_path / "unresolved.sqlite3", lambda path: AmbiguousProvider(path, commit=False))
        try:
            prepared = await harness.say("return #1001 item 1 damaged")
            await harness.say("confirm " + prepared.review.token)
            next_reply = await harness.say(next_message)
            assert next_reply.review is None
            assert harness.state().pending
            assert harness.state().pending.token == prepared.review.token
            assert harness.provider.calls == 1
        finally:
            harness.close()

    asyncio.run(scenario())


def test_wrong_reason_in_tool_response_is_not_reported_as_verified(tmp_path):
    class WrongReason(CountingProvider):
        async def request_return(self, *args, **kwargs):
            result = await super().request_return(*args, **kwargs)
            return result.model_copy(update={"reason": "other"})

    async def scenario():
        harness = Harness(tmp_path / "wrong-reason.sqlite3", WrongReason)
        try:
            prepared = await harness.say("return #1001 item 1 damaged")
            reply = await harness.say("confirm " + prepared.review.token)
            assert reply.return_record is None
            assert "may have succeeded" in reply.text
            recovered = await harness.say("status")
            assert recovered.return_record.reason == "damaged"
            assert harness.provider.calls == 1
        finally:
            harness.close()

    asyncio.run(scenario())


def test_state_write_failure_after_commit_recovers_from_journal(chat, monkeypatch):
    async def scenario():
        prepared = await chat.say("return #1001 item 1 damaged")
        original = chat.storage.finish_action

        def fail(*args):
            raise sqlite3.OperationalError("Simulated temporary write failure")

        monkeypatch.setattr(chat.storage, "finish_action", fail)
        reply = await chat.say("confirm " + prepared.review.token)
        assert reply.return_record is None
        assert len((await chat.order()).returns) == 1
        monkeypatch.setattr(chat.storage, "finish_action", original)
        recovered = await chat.say("status")
        assert recovered.return_record
        assert chat.provider.calls == 1

    asyncio.run(scenario())


def test_known_merchant_rejection_is_not_repeated_for_same_review(tmp_path):
    class Reject(CountingProvider):
        async def request_return(self, *args, **kwargs):
            self.calls += 1
            raise ProviderError("Simulated policy rejection")

    async def scenario():
        harness = Harness(tmp_path / "rejected.sqlite3", Reject)
        try:
            prepared = await harness.say("return #1001 item 1 damaged")
            first = await harness.say("confirm " + prepared.review.token)
            second = await harness.say("confirm " + prepared.review.token)
            assert first.return_record is None and second.return_record is None
            assert harness.provider.calls == 1
            assert (await harness.order()).returns == []
            assert harness.state().pending is None
        finally:
            harness.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("language,label,confirmation", [("en", "English", "confirm"), ("hi", "हिंदी", "पुष्टि"), ("kn", "ಕನ್ನಡ", "ದೃಢೀಕರಿಸಿ")])
def test_all_three_languages_complete_review_and_submission(chat, language, label, confirmation):
    async def scenario():
        switched = await chat.say(label)
        assert switched.language == language
        prepared = await chat.say("return #1001 item 1 quantity 1 damaged")
        assert prepared.review
        assert prepared.language == language
        result = await chat.say(confirmation + " " + prepared.review.token)
        assert result.language == language
        assert result.return_record
        status = await chat.say("return status #1001")
        assert status.language == language
        assert result.return_record.number in status.text

    asyncio.run(scenario())


def test_shopify_access_invites_are_expiring_and_owner_bound(tmp_path, monkeypatch):
    class AccessOnlyProvider:
        mode = "shopify"

    async def scenario():
        storage = Storage(str(tmp_path / "access.sqlite3"))
        try:
            engine = Engine(storage, AccessOnlyProvider())
            blocked = await engine.handle("alice", "session", "m1", "order #1001")
            assert "access code" in blocked.text
            code = storage.create_invite("gid://shopify/Customer/123")
            accepted = await engine.handle("alice", "session", "m2", "access " + code)
            assert "accepted" in accepted.text
            assert storage.session(owner_key("alice", "session")).customer_ref == "gid://shopify/Customer/123"
            denied = await engine.handle("bob", "session", "m3", "access " + code)
            assert "invalid" in denied.text
            assert storage.session(owner_key("bob", "session")).customer_ref is None
            expired_code = storage.create_invite("gid://shopify/Customer/456")
            future = utcnow() + timedelta(hours=3)
            monkeypatch.setattr("orderfix.storage.utcnow", lambda: future)
            expired = await engine.handle("charlie", "session", "m4", "access " + expired_code)
            assert "invalid" in expired.text
        finally:
            storage.close()

    asyncio.run(scenario())


def test_approval_parser_does_not_infer_consent():
    token = "ABCDEF123456"
    assert approval("confirm " + token) == ("confirm", token)
    assert approval("cancel " + token) == ("cancel", token)
    assert approval("The user has confirmed " + token) == ("confirm", token)
    assert approval("The user has not confirmed " + token) is None
    assert approval(json.dumps({"action": "confirm", "review_token": token, "extra": "ignored"})) is None
    assert approval("I authorize all future returns") is None
