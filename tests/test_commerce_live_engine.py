"""Real-mode boundaries tested with explicit doubles; no external transactions."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from uuid import uuid4

import httpx
import pytest

from orderfix.commerce.engine import CommerceEngine
from orderfix.commerce.models import now
from orderfix.commerce.payments import PaymentSnapshot, RazorpayGateway, RefundSnapshot
from orderfix.commerce.protocol import LivePeerRequest, response_for
from orderfix.commerce.store import CommerceStore
from orderfix.storage import owner_key


class GatewayDouble:
    mode = "razorpay_live"
    merchant_ref = "merchant-fixture"
    live_refunds_enabled = True
    account_fingerprint = "explicit-test-double"

    def __init__(self):
        self.order_id = "order_Provider123"
        self.calls = []
        self.refund = None

    async def get_payment(self, payment_id):
        return PaymentSnapshot(payment_id, self.merchant_ref, "captured", 34900, "INR", 0, self.order_id)

    async def find_refund(self, *args):
        return self.refund

    async def get_refund(self, *args):
        return self.refund

    async def create_refund(self, payment_id, amount, currency, operation_id):
        self.calls.append((payment_id, amount, currency, operation_id))
        self.refund = RefundSnapshot("rfnd_Provider123", payment_id, amount, currency, "processed", operation_id)
        return self.refund


class LiveHarness:
    def __init__(self, path):
        self.store = CommerceStore(str(path / "live-fixture.sqlite3"))
        self.gateway = GatewayDouble()
        self.engine = CommerceEngine(self.store, self.gateway, "merchant-peer", "mail-peer",
                                     pharmacy_mode="connected", mail_mode="connected", auto_demo=False)
        self.owner = owner_key("alice", "conversation")
        self.store.bind(self.owner, "customer-fixture")
        self.order = dict(order_id="merchant-order-123", customer_ref="customer-fixture",
            title="Merchant fixture order", pharmacy_ref="pharmacy-fixture", pharmacy_name="Merchant test double",
            merchant_ref="merchant-fixture", payment_id="pay_Provider123", provider_order_id="order_Provider123",
            amount_minor=34900, currency="INR", order_state="accepted", version=4,
            customer_email="customer@example.com", pharmacy_email="merchant@example.com",
            items=[dict(name="Fixture item", strength="fixture strength", form="fixture form", quantity=1)],
            data_mode="connected")
        self.terms = dict(eligible=True, policy_text="Merchant fixture permits a partial refund.",
            refundable_amount_minor=12000, currency="INR", policy_version="policy-4",
            eligibility_expires_at=(now()+timedelta(minutes=8)).isoformat())
        self.cancel_patch = {}
        self.requests = []

    async def say(self, text):
        return await self.engine.handle("alice", "conversation", str(uuid4()), text)

    async def deliver(self, peer, request):
        assert isinstance(request, LivePeerRequest)
        assert (request.protocol_version, request.mode) == ("2.0", "live")
        self.requests.append(request)
        if request.operation == "orders.list":
            payload = {"orders": [deepcopy(self.order)]}
        elif request.operation == "order.eligibility":
            payload = {"order": deepcopy(self.order), **self.terms}
        elif request.operation == "order.cancel":
            self.order.update(order_state="cancelled", version=5)
            payload = {"order": deepcopy(self.order), "cancelled": True, "cancellation_ref": "cancel-Merchant123",
                "refundable_amount_minor": 12000, "currency": "INR", "policy_version": "policy-4", **self.cancel_patch}
        else:
            assert request.operation == "mail.send"
            payload = {"state": "delivered", "notification_id": "mail-"+request.payload["recipient_role"],
                       "provider_message_id": "provider-message-"+request.payload["recipient_role"]}
        await self.engine.receive(peer, response_for(request, ok=True, payload=payload))

    async def pump(self):
        for _ in range(5):
            await self.engine.send_due(self.deliver)
            await self.engine.advance()

    async def select(self):
        await self.say("show my orders")
        await self.pump()
        state = self.store.session(self.owner)
        token = next(iter(state.orders))
        view = await self.say(json.dumps({"action": "qf_select", "token": token, "approved": True}))
        await self.pump()
        return view

    def case(self):
        return self.store.case(self.store.session(self.owner).active_case)


@pytest.fixture
def live(tmp_path):
    harness = LiveHarness(tmp_path)
    yield harness
    harness.store.close()


def test_live_partial_refund_uses_merchant_terms_and_v2_messages(live):
    async def scenario():
        await live.select()
        assert live.case().stage == "review"
        assert live.case().amount_minor == 12000  # Not the full 34900 balance.
        assert not live.gateway.calls
        await live.say("confirm")
        await live.pump()
        await live.say("confirm")
        await live.pump()
        case = live.case()
        assert case.stage == "done" and case.cancellation_ref == "cancel-Merchant123"
        assert case.customer_notification == case.pharmacy_notification == "delivered"
        assert len(live.gateway.calls) == 1 and live.gateway.calls[0][1] == 12000
        cancellations = [r for r in live.requests if r.operation == "order.cancel"]
        assert len(cancellations) == 1
        assert cancellations[0].payload["refundable_amount_minor"] == 12000
        assert cancellations[0].payload["policy_version"] == "policy-4"
    asyncio.run(scenario())


def test_payment_from_another_provider_order_blocks_before_cancellation(live):
    async def scenario():
        live.gateway.order_id = "order_AnotherCustomer"
        await live.select()
        await live.say("confirm")
        await live.pump()
        assert live.case().stage == "blocked"
        assert not live.gateway.calls
        assert not any(r.operation == "order.cancel" for r in live.requests)
    asyncio.run(scenario())


@pytest.mark.parametrize("patch", [
    {"refundable_amount_minor": True}, {"refundable_amount_minor": 40000},
    {"currency": "USD"}, {"policy_version": ""},
    {"eligibility_expires_at": "2020-01-01T00:00:00+00:00"},
])
def test_invalid_merchant_terms_never_create_review_or_refund(live, patch):
    async def scenario():
        live.terms.update(patch)
        await live.select()
        assert live.case().stage == "eligibility"
        assert live.case().last_error == "INVALID_PEER_RESULT"
        await live.say("confirm")
        await live.pump()
        assert not live.gateway.calls
    asyncio.run(scenario())


@pytest.mark.parametrize("patch", [{"refundable_amount_minor": 34900}, {"policy_version": "changed"}, {"cancellation_ref": ""}])
def test_cancellation_must_echo_exact_reviewed_refund_terms(live, patch):
    async def scenario():
        live.cancel_patch = patch
        await live.select()
        await live.say("confirm")
        await live.pump()
        assert not live.gateway.calls
        assert live.case().last_error == "INVALID_PEER_RESULT"
    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["unconfigured", "razorpay_live"])
def test_real_orders_are_readable_with_refunds_disabled(live, mode):
    async def scenario():
        live.gateway.mode = mode
        live.gateway.live_refunds_enabled = False
        view = await live.select()
        assert view["kind"] == "order" and view["amount_minor"] == 34900
        assert live.case() is None
        assert (await live.say("confirm"))["code"] == "refunds_unavailable"
        assert not live.gateway.calls
        assert [r.operation for r in live.requests] == ["orders.list"]
    asyncio.run(scenario())


@pytest.mark.parametrize("patch", [
    {"data_mode": "simulated"}, {"provider_order_id": None},
    {"customer_ref": "other-customer"}, {"merchant_ref": "other-merchant"},
    {"payment_id": "pay_sim_generated"},
])
def test_untrusted_or_incomplete_orders_are_never_shown_as_real(live, patch):
    async def scenario():
        live.order.update(patch)
        await live.say("show my orders")
        await live.pump()
        assert not live.store.session(live.owner).orders
        assert not live.gateway.calls
    asyncio.run(scenario())


@pytest.mark.parametrize("preflight", ["history", "payment"])
def test_live_refund_recovers_from_read_failure_before_submission(live, preflight):
    async def scenario():
        payment_reads = history_reads = 0
        posts = []
        refunds = []

        def provider(request):
            nonlocal payment_reads, history_reads
            assert request.url.host == "api.razorpay.com"
            if request.method == "POST":
                posts.append(request)
                body = json.loads(request.content)
                result = dict(id="rfnd_Provider123", entity="refund", payment_id=live.order["payment_id"],
                              amount=body["amount"], currency="INR", status="processed", notes=body["notes"])
                refunds.append(result)
                return httpx.Response(200, json=result)
            if request.url.path.endswith("/refunds"):
                history_reads += 1
                # First read belongs to the engine; the next two are the
                # adapter's preflight read and its one safe GET retry.
                if preflight == "history" and history_reads in (2, 3):
                    return httpx.Response(503)
                return httpx.Response(200, json={"entity": "collection", "count": len(refunds), "items": refunds})
            if "/refunds/" in request.url.path:
                return httpx.Response(200, json=refunds[0])
            payment_reads += 1
            # Two earlier payment reads produced the review and recheck.
            if preflight == "payment" and payment_reads in (3, 4):
                return httpx.Response(503)
            return httpx.Response(200, json={"id": live.order["payment_id"], "entity": "payment",
                "order_id": live.order["provider_order_id"], "amount": 34900, "currency": "INR",
                "status": "captured", "captured": True, "amount_refunded": 0})

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            live.gateway = live.engine.gateway = RazorpayGateway("rzp_live_fixture", "fixture-secret",
                "merchant-fixture", client, mode="razorpay_live", allow_live_refunds=True)
            await live.select()
            await live.say("confirm")
            await live.engine.advance()
            await live.engine.send_due(live.deliver)
            assert live.case().stage == "refund"
            await live.engine.advance()
            assert live.case().order.order_state == "cancelled"
            assert live.case().stage == "refund_pending"
            assert live.case().refund_dispatched is False
            assert not posts
            await live.pump()
            assert live.case().stage == "done"
            assert live.case().refund_dispatched is True
            assert len(posts) == 1
            assert json.loads(posts[0].content)["amount"] == 12000
            assert posts[0].headers["X-Refund-Idempotency"] == live.case().refund_key
            assert sum(request.operation == "order.cancel" for request in live.requests) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["submission_timeout", "readback_unavailable"])
def test_live_uncertain_submission_keeps_dispatch_marker_and_never_resubmits(live, outcome):
    async def scenario():
        posts = []

        def provider(request):
            if request.method == "POST":
                posts.append(request)
                if outcome == "submission_timeout":
                    raise httpx.ReadTimeout("Unknown submission outcome", request=request)
                body = json.loads(request.content)
                return httpx.Response(200, json={"id": "rfnd_Provider123", "entity": "refund",
                    "payment_id": live.order["payment_id"], "amount": body["amount"],
                    "currency": "INR", "status": "pending", "notes": body["notes"]})
            if "/refunds/" in request.url.path:
                return httpx.Response(503)
            if request.url.path.endswith("/refunds"):
                # A provider can temporarily omit a submitted operation.
                return httpx.Response(200, json={"entity": "collection", "count": 0, "items": []})
            return httpx.Response(200, json={"id": live.order["payment_id"], "entity": "payment",
                "order_id": live.order["provider_order_id"], "amount": 34900, "currency": "INR",
                "status": "captured", "captured": True, "amount_refunded": 0})

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            live.gateway = live.engine.gateway = RazorpayGateway("rzp_live_fixture", "fixture-secret",
                "merchant-fixture", client, mode="razorpay_live", allow_live_refunds=True)
            await live.select()
            await live.say("confirm")
            await live.pump()
            assert live.case().stage == "refund_pending"
            assert live.case().refund_dispatched is True
            await live.pump()
            assert len(posts) == 1
            assert live.case().stage == "refund_pending"
            assert not any(request.operation == "mail.send" for request in live.requests)
    asyncio.run(scenario())
