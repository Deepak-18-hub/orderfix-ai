"""Connected-mail contracts using fabricated allowlisted replies; no email sent."""
import asyncio
from dataclasses import replace

import pytest

from test_commerce_engine import CountingGateway, Harness, MAIL, MED
from orderfix.commerce.engine import CommerceEngine
from orderfix.commerce.protocol import PeerRequest, response_for
from orderfix.storage import owner_key


class PendingThenProcessed(CountingGateway):
    """Keep a simulated provider record pending until the test advances it."""
    def __init__(self, path):
        super().__init__(path)
        self.processed = False

    async def create_refund(self, *args, **kwargs):
        result = await super().create_refund(*args, **kwargs)
        return replace(result, status="pending")

    async def get_refund(self, *args, **kwargs):
        result = await super().get_refund(*args, **kwargs)
        return result if self.processed else replace(result, status="pending")


class ConnectedMailHarness(Harness):
    def __init__(self, path, gateway_class=CountingGateway):
        super().__init__(path, gateway_class, auto_demo=False)
        self.engine = CommerceEngine(self.store, self.gateway, MED, MAIL,
                                    pharmacy_mode="simulated", mail_mode="connected", auto_demo=False)
        # Explicit trusted account setup; chat-supplied order IDs are not identity.
        self.store.bind(owner_key("alice", "chat"), "authorized-mail-test-customer")
        self.pharmacy.seed_customer("authorized-mail-test-customer")
        self.mail_requests = []

    async def send(self, target, request):
        if target == MAIL:
            self.requests.append((target, request))
            self.mail_requests.append(request)
            return
        await super().send(target, request)

    async def finish_refund(self):
        review = await self.review()
        await self.approve(review)
        await self.pump()
        assert len(self.refunds()) == 1
        assert len(self.mail_requests) == 2
        assert not self.captures()  # Connected-mail test never uses the capture service.

    def initial(self, role, refund_status="processed"):
        return next(request for request in self.mail_requests if request.operation == "mail.send"
                    and request.payload["recipient_role"] == role
                    and request.payload["refund"]["status"] == refund_status)

    async def reply(self, request, state, notification_id, provider_message_id="provider-synthetic-001"):
        payload = {"state": state, "notification_id": notification_id,
                   "provider_message_id": provider_message_id, "evidence_mode": "connected"}
        return await self.engine.receive(MAIL, response_for(request, ok=True, payload=payload))

    def polls(self):
        return [row for row in self.store.conn.execute("SELECT * FROM qf_commands WHERE state IN ('queued','sent')").fetchall()
                if PeerRequest.parse_raw(row["body"]).operation == "mail.status"]

    async def next_poll(self, notification_id):
        row = next(row for row in self.polls()
                   if PeerRequest.parse_raw(row["body"]).payload["notification_id"] == notification_id)
        request = PeerRequest.parse_raw(row["body"])
        assert request.payload == {"notification_id": notification_id}
        self.make_retries_due()
        await self.deliver()
        assert any(sent.request_id == request.request_id for sent in self.mail_requests)
        return request


def test_queued_accepted_delivered_polls_only_notification_reference(tmp_path):
    async def scenario():
        chat = ConnectedMailHarness(tmp_path)
        try:
            await chat.finish_refund()
            assert await chat.reply(chat.initial("pharmacy"), "delivered", "notification-pharmacy")
            assert await chat.reply(chat.initial("customer"), "queued", "notification-customer", None)
            assert chat.case().customer_notification == "queued"
            rows = chat.polls()
            assert len(rows) == 1
            assert rows[0]["next_at"] > 0
            count = len(chat.mail_requests)
            await chat.deliver()
            assert len(chat.mail_requests) == count, "Polls must respect their delay"
            first_poll = await chat.next_poll("notification-customer")
            assert await chat.reply(first_poll, "accepted", "notification-customer")
            assert chat.case().customer_notification == "accepted"
            second_poll = await chat.next_poll("notification-customer")
            assert second_poll.request_id != first_poll.request_id
            assert await chat.reply(second_poll, "delivered", "notification-customer")
            assert chat.case().customer_notification == "delivered"
            assert not chat.polls()
            assert await chat.reply(second_poll, "delivered", "notification-customer")
            assert not chat.polls(), "Repeated receipt must not create another poll"
            assert chat.gateway.refund_calls == 1
            assert sum(request.operation == "order.cancel" for _, request in chat.requests) == 1
        finally:
            chat.close()
    asyncio.run(scenario())


def test_delivery_poll_recovers_original_recipient_after_restart(tmp_path):
    async def scenario():
        first = ConnectedMailHarness(tmp_path)
        try:
            await first.finish_refund()
            await first.reply(first.initial("pharmacy"), "delivered", "pharmacy-delivered")
            await first.reply(first.initial("customer"), "queued", "customer-survives-restart", None)
            case_id = first.case().id
        finally:
            first.close()
        recovered = ConnectedMailHarness(tmp_path)
        try:
            poll = await recovered.next_poll("customer-survives-restart")
            assert await recovered.reply(poll, "accepted", "customer-survives-restart")
            next_poll = await recovered.next_poll("customer-survives-restart")
            assert await recovered.reply(next_poll, "delivered", "customer-survives-restart")
            case = recovered.store.case(case_id)
            assert case.customer_notification == case.pharmacy_notification == "delivered"
            assert case.refund_state == "processed"
            assert recovered.gateway.refund_calls == 0
            assert len(recovered.refunds()) == 1
            assert not recovered.polls()
        finally:
            recovered.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("bad_notification", ["different-notification", "", None, 123])
def test_status_reply_cannot_change_notification_identity(tmp_path, bad_notification):
    async def scenario():
        chat = ConnectedMailHarness(tmp_path)
        try:
            await chat.finish_refund()
            await chat.reply(chat.initial("pharmacy"), "delivered", "pharmacy")
            await chat.reply(chat.initial("customer"), "queued", "customer", None)
            poll = await chat.next_poll("customer")
            assert await chat.reply(poll, "delivered", bad_notification) is False
            assert chat.case().customer_notification == "queued"
            assert chat.case().last_error == "INVALID_PEER_RESULT"
            assert chat.store.command(poll.request_id)["state"] != "answered"
            assert chat.gateway.refund_calls == 1
        finally:
            chat.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("state", ["accepted", "delivered"])
def test_provider_reference_required_for_delivery_evidence(tmp_path, state):
    async def scenario():
        chat = ConnectedMailHarness(tmp_path)
        try:
            await chat.finish_refund()
            request = chat.initial("customer")
            assert await chat.reply(request, state, "customer", None) is False
            assert chat.case().customer_notification == "queued"
            assert not chat.polls()
            assert chat.case().last_error == "INVALID_PEER_RESULT"
            assert chat.gateway.refund_calls == 1
        finally:
            chat.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("terminal_state", ["failed", "bounced"])
def test_terminal_delivery_failure_stops_polling_without_repeating_refund(tmp_path, terminal_state):
    async def scenario():
        chat = ConnectedMailHarness(tmp_path)
        try:
            await chat.finish_refund()
            await chat.reply(chat.initial("pharmacy"), "delivered", "pharmacy")
            await chat.reply(chat.initial("customer"), "accepted", "customer")
            poll = await chat.next_poll("customer")
            assert await chat.reply(poll, terminal_state, "customer", None)
            assert chat.case().customer_notification == terminal_state
            assert not chat.polls()
            await chat.pump()
            assert chat.gateway.refund_calls == 1
            assert chat.case().refund_state == "processed"
        finally:
            chat.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("stale_result", ["delivered", "failed", "permanent_error"])
def test_old_pending_refund_receipt_cannot_overwrite_processed_receipt(tmp_path, stale_result):
    async def scenario():
        chat = ConnectedMailHarness(tmp_path, PendingThenProcessed)
        try:
            await chat.finish_refund()
            assert chat.case().refund_state == "pending"
            await chat.reply(chat.initial("pharmacy", "pending"), "delivered", "old-pharmacy")
            await chat.reply(chat.initial("customer", "pending"), "queued", "old-customer", None)
            old_poll = await chat.next_poll("old-customer")
            chat.gateway.processed = True
            await chat.pump()
            assert chat.case().refund_state == "processed"
            await chat.reply(chat.initial("pharmacy", "processed"), "delivered", "new-pharmacy")
            await chat.reply(chat.initial("customer", "processed"), "delivered", "new-customer")
            assert chat.case().customer_notification == "delivered"
            if stale_result == "permanent_error":
                await chat.engine.receive(MAIL, response_for(old_poll, ok=False, error_code="DELIVERY_FAILED"))
            else:
                await chat.reply(old_poll, stale_result, "old-customer")
            assert chat.case().customer_notification == "delivered"
            assert chat.case().pharmacy_notification == "delivered"
            assert chat.case().last_error != "EMAIL_FAILED"
            assert chat.gateway.refund_calls == 1
        finally:
            chat.close()
    asyncio.run(scenario())
