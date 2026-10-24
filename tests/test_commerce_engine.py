"""Commerce integration tests with real journals and durable simulated peers.

No network gateway, teammate agent, or external email is contacted here.
"""
import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest

from orderfix.commerce.engine import CommerceEngine
from orderfix.commerce.models import now
from orderfix.commerce.payments import PaymentAmbiguous, SimulatedGateway
from orderfix.commerce.peers import MailService, PharmacyService
from orderfix.commerce.store import CommerceStore
from orderfix.storage import owner_key


MED = "medguardian-test-peer"
MAIL = "notification-test-peer"


class CountingGateway(SimulatedGateway):
    def __init__(self, path):
        super().__init__(path)
        self.refund_calls = 0

    async def create_refund(self, *args, **kwargs):
        self.refund_calls += 1
        return await super().create_refund(*args, **kwargs)


class LostRefundResponse(CountingGateway):
    """The provider commits but the coordinator loses the successful response."""
    async def create_refund(self, *args, **kwargs):
        await super().create_refund(*args, **kwargs)
        raise PaymentAmbiguous("Response lost after provider commit")


class UnknownRefundOutcome(CountingGateway):
    """Absence in a read is insufficient to safely retry an ambiguous write."""
    async def create_refund(self, *args, **kwargs):
        self.refund_calls += 1
        raise PaymentAmbiguous("Outcome unknown")


class Harness:
    def __init__(self, path, gateway_class=CountingGateway, auto_demo=True):
        self.path = path
        self.store = CommerceStore(str(path / "coordinator.sqlite3"))
        self.gateway = gateway_class(path / "gateway.sqlite3")
        self.pharmacy = PharmacyService(path / "pharmacy.sqlite3", auto_seed=True)
        self.mail = MailService(path / "mail.sqlite3")
        self.engine = CommerceEngine(self.store, self.gateway, MED, MAIL, auto_demo=auto_demo)
        self.requests = []
        self.inbox = []
        self.drop_once = set()
        self.offline = set()

    async def say(self, text, sender="alice", session="chat", message_id=None):
        return await self.engine.handle(sender, session, message_id or str(uuid4()), text)

    def state(self, sender="alice", session="chat"):
        return self.store.session(owner_key(sender, session))

    def case(self, sender="alice", session="chat"):
        state = self.state(sender, session)
        assert state.active_case is not None
        case = self.store.case(state.active_case)
        assert case is not None
        return case

    async def send(self, target, request):
        self.requests.append((target, request))
        if target in self.offline:
            raise ConnectionError("Simulated peer offline")
        peer = self.pharmacy if target == MED else self.mail if target == MAIL else None
        assert peer is not None, "Unexpected destination"
        response = peer.handle(request)
        if request.operation in self.drop_once:
            self.drop_once.remove(request.operation)
            return
        self.inbox.append((target, response))

    async def deliver(self):
        await self.engine.send_due(self.send)
        responses, self.inbox = self.inbox, []
        for target, response in responses:
            await self.engine.receive(target, response)

    async def pump(self, rounds=8):
        for _ in range(rounds):
            await self.deliver()
            await self.engine.advance()
            await self.deliver()

    def make_retries_due(self):
        with self.store.conn:
            self.store.conn.execute("UPDATE qf_commands SET next_at=0 WHERE state IN ('queued','sent')")

    async def orders(self, sender="alice", session="chat"):
        await self.say("show my orders", sender, session)
        await self.pump()
        view = self.state(sender, session).view
        assert view["kind"] == "orders", view
        return view["orders"]

    async def review(self, sender="alice", session="chat", index=0):
        orders = await self.orders(sender, session)
        await self.say(json.dumps({"action": "qf_select", "token": orders[index]["token"], "approved": True}), sender, session)
        await self.pump()
        view = await self.say("check status", sender, session)
        assert view["kind"] == "review", view
        assert not self.refunds(), "Selecting an order must never execute a refund"
        return view

    async def approve(self, review, sender="alice", session="chat", message_id=None):
        return await self.say(json.dumps({"action": "qf_approve", "token": review["token"], "approved": True}), sender, session, message_id)

    def refunds(self):
        return self.gateway._db.execute("SELECT * FROM quickfix_sim_refunds").fetchall()

    def captures(self):
        return self.mail._db.execute("SELECT * FROM peer_mail_captures").fetchall()

    def close(self):
        self.store.close()
        self.gateway.close()
        self.pharmacy.close()
        self.mail.close()


@pytest.fixture
def chat(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    harness.close()


def test_complete_cancellation_refund_and_two_captured_receipts(chat):
    async def scenario():
        review = await chat.review()
        assert chat.case().stage == "review"
        assert chat.gateway.refund_calls == 0
        assert chat.captures() == []
        await chat.approve(review)
        await chat.pump()
        case = chat.case()
        assert case.order.order_state == "cancelled"
        assert case.stage == "done"
        assert case.refund_state == "processed"
        assert case.refund_id
        assert case.customer_notification == case.pharmacy_notification == "captured"
        assert chat.gateway.refund_calls == len(chat.refunds()) == 1
        assert len(chat.captures()) == 2
        assert {r["recipient_role"] for r in chat.captures()} == {"customer", "pharmacy"}
        payment = await chat.gateway.get_payment(case.order.payment_id)
        assert payment.amount_refunded == case.amount_minor == case.order.amount_minor
        status = await chat.say("check status")
        assert status["kind"] == "status"
        assert status["refund_state"] == "processed"
        assert status["gateway_mode"] == "simulated"
        assert status["pharmacy_mode"] == "simulated"
        assert status["mail_mode"] == "captured"

    asyncio.run(scenario())


def test_replayed_message_and_repeated_approval_do_not_repeat_refund(chat):
    async def scenario():
        review = await chat.review()
        await chat.approve(review, message_id="same-approval-message")
        await chat.approve(review, message_id="same-approval-message")
        await chat.pump()
        refund_id = chat.case().refund_id
        await chat.approve(review)
        await chat.say("confirm")
        await chat.pump()
        assert chat.case().refund_id == refund_id
        assert chat.gateway.refund_calls == len(chat.refunds()) == 1
        assert len(chat.captures()) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("text", ["do not approve", "confirm later", "I have not approved", "पुष्टि न करें", "ದೃಢೀಕರಿಸಬೇಡಿ"])
def test_negative_or_ambiguous_text_cannot_execute_current_review(chat, text):
    async def scenario():
        await chat.review()
        await chat.say(text)
        await chat.pump()
        assert chat.gateway.refund_calls == 0
        assert chat.refunds() == []
        assert chat.captures() == []
        assert not any(request.operation == "order.cancel" for _, request in chat.requests)

    asyncio.run(scenario())


def test_tampered_approval_amount_is_rejected(chat):
    async def scenario():
        review = await chat.review()
        await chat.say(json.dumps({"action": "qf_approve", "token": review["token"], "amount_minor": 1}))
        await chat.pump()
        assert chat.gateway.refund_calls == 0
        assert chat.case().approval_ref is None

    asyncio.run(scenario())


def test_expired_review_does_not_cancel_or_refund(chat):
    async def scenario():
        review = await chat.review()
        case = chat.case()
        case.expires_at = now() - timedelta(seconds=1)
        chat.store.save_case(case)
        await chat.approve(review)
        await chat.pump()
        assert chat.gateway.refund_calls == 0
        assert not any(request.operation == "order.cancel" for _, request in chat.requests)

    asyncio.run(scenario())


def test_customers_and_conversations_have_isolated_order_choices_and_approvals(chat):
    async def scenario():
        review = await chat.review()
        alice = chat.case()
        bob_orders = await chat.orders("bob", "chat")
        another_chat = await chat.orders("alice", "other-chat")
        assert chat.state("bob").customer_ref != alice.customer_ref
        assert chat.state("alice", "other-chat").customer_ref != alice.customer_ref
        assert {o.order_id for o in chat.state("bob").orders.values()}.isdisjoint({alice.order.order_id})
        assert {o["token"] for o in bob_orders}.isdisjoint({o["token"] for o in another_chat})
        await chat.approve(review, "bob", "chat")
        await chat.approve(review, "alice", "other-chat")
        await chat.pump()
        assert chat.gateway.refund_calls == 0
        assert chat.store.case(alice.id).approval_ref is None

    asyncio.run(scenario())


def test_dispatched_order_is_rejected_before_payment_mutation(chat):
    async def scenario():
        orders = await chat.orders()
        dispatched = next(o for o in orders if o["order_state"] == "dispatched")
        await chat.say(json.dumps({"action": "qf_select", "token": dispatched["token"]}))
        await chat.pump()
        assert chat.case().stage == "rejected"
        assert chat.case().last_error == "NOT_ELIGIBLE"
        assert chat.gateway.refund_calls == 0
        assert chat.captures() == []

    asyncio.run(scenario())


def test_draft_back_is_not_an_order_cancellation(chat):
    async def scenario():
        await chat.review()
        await chat.say("back")
        await chat.say("confirm")
        await chat.pump()
        assert chat.gateway.refund_calls == 0
        assert not any(request.operation == "order.cancel" for _, request in chat.requests)

    asyncio.run(scenario())


def test_lost_pharmacy_reply_recovers_same_operation_after_restart(tmp_path):
    async def scenario():
        first = Harness(tmp_path)
        try:
            review = await first.review()
            first.drop_once.add("order.cancel")
            await first.approve(review)
            await first.pump()
            case_id = first.case().id
            assert first.case().stage == "cancelling"
            assert first.gateway.refund_calls == 0
        finally:
            first.close()
        recovered = Harness(tmp_path)
        try:
            recovered.make_retries_due()
            await recovered.pump()
            case = recovered.store.case(case_id)
            assert case.stage == "done"
            assert case.order.version == 2
            assert case.refund_state == "processed"
            assert recovered.gateway.refund_calls == len(recovered.refunds()) == 1
            assert len(recovered.captures()) == 2
        finally:
            recovered.close()

    asyncio.run(scenario())


def test_committed_refund_with_lost_response_is_read_back_without_resubmission(tmp_path):
    async def scenario():
        chat = Harness(tmp_path, LostRefundResponse)
        try:
            review = await chat.review()
            await chat.approve(review)
            await chat.pump()
            assert chat.case().stage == "done"
            assert chat.case().refund_state == "processed"
            assert chat.gateway.refund_calls == len(chat.refunds()) == 1
            assert len(chat.captures()) == 2
        finally:
            chat.close()

    asyncio.run(scenario())


def test_unknown_refund_outcome_is_never_blindly_retried(tmp_path):
    async def scenario():
        chat = Harness(tmp_path, UnknownRefundOutcome)
        try:
            review = await chat.review()
            await chat.approve(review)
            await chat.pump()
            await chat.say("check status")
            await chat.approve(review)
            await chat.pump()
            assert chat.gateway.refund_calls == 1
            assert chat.refunds() == []
            assert chat.case().refund_state != "processed"
        finally:
            chat.close()

    asyncio.run(scenario())


def test_email_outage_recovers_without_repeating_refund(chat):
    async def scenario():
        review = await chat.review()
        chat.offline.add(MAIL)
        await chat.approve(review)
        await chat.pump()
        assert chat.case().refund_state == "processed"
        assert chat.gateway.refund_calls == 1
        assert chat.captures() == []
        chat.offline.remove(MAIL)
        chat.make_retries_due()
        await chat.pump()
        assert len(chat.captures()) == 2
        assert chat.case().customer_notification == chat.case().pharmacy_notification == "captured"
        assert chat.gateway.refund_calls == len(chat.refunds()) == 1

    asyncio.run(scenario())


def test_non_demo_users_require_authorized_binding(tmp_path):
    async def scenario():
        chat = Harness(tmp_path, auto_demo=False)
        try:
            response = await chat.say("show my orders")
            assert response["kind"] == "notice"
            assert response["code"] == "auth_required"
            assert chat.store.due() == []
            assert chat.state().customer_ref is None
        finally:
            chat.close()

    asyncio.run(scenario())
