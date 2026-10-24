"""Merchant contract tests use injected transports; no merchant is contacted."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from uagents import Model

from orderfix.commerce.merchant import MerchantAPIAdapter, make_merchant_protocol
from orderfix.commerce.protocol import LivePeerRequest, LivePeerResponse, PeerRequest, PeerValidationError


def request(operation="order.get", payload=None, **changes):
    now = datetime.now(timezone.utc)
    fields = dict(request_id=str(uuid4()), correlation_id="corr-1", case_id="case-1",
                  customer_ref="customer-1", operation=operation, idempotency_key="business-1",
                  created_at=now, expires_at=now + timedelta(minutes=5), expected_version=4,
                  payload=({} if operation == "orders.list" else {"order_id": "merchant-1"}) if payload is None else payload)
    fields.update(changes)
    return LivePeerRequest(**fields)


def order(**changes):
    # All identities and amounts in this unit fixture are synthetic test input.
    fields = dict(order_id="merchant-1", customer_ref="customer-1", title="Contract test order",
                  pharmacy_ref="pharmacy-1", pharmacy_name="Test merchant", merchant_ref="merchant-account-1",
                  payment_id="pay_fixture1", provider_order_id="order_fixture1", amount_minor=12345,
                  currency="INR", order_state="accepted", version=2, customer_email="c@example.invalid",
                  pharmacy_email="p@example.invalid", items=[dict(name="Test item", strength="test strength",
                  form="test form", quantity=2)], data_mode="connected")
    fields.update(changes)
    return fields


def eligibility(**changes):
    fields = dict(eligible=True, order=order(), policy_text="Merchant policy supplied in test fixture",
                  refundable_amount_minor=12000, currency="INR", policy_version="policy-3",
                  eligibility_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())
    fields.update(changes)
    return fields


def cancellation_payload(**changes):
    fields = dict(order_id="merchant-1", expected_order_version=2, approval_ref="approval-1",
                  refundable_amount_minor=12000, currency="INR", policy_version="policy-3",
                  eligibility_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())
    fields.update(changes)
    return fields


def cancelled(**changes):
    fields = dict(order=order(order_state="cancelled", version=3), cancellation_ref="merchant-cancel-1",
                  refundable_amount_minor=12000, currency="INR", policy_version="policy-3")
    fields.update(changes)
    return fields


def adapter(result, calls=None, status=200):
    calls = [] if calls is None else calls

    def route(req):
        calls.append(req)
        return httpx.Response(status, json=result)

    return MerchantAPIAdapter("https://merchant.example.invalid/api", "test-secret", transport=httpx.MockTransport(route))


@pytest.mark.parametrize("url,token", [
    ("", "secret"), ("http://merchant.test", "secret"), ("https://user:secret@merchant.test", "secret"),
    ("https://merchant.test?token=secret", "secret"), ("https://merchant.test#path", "secret"),
    ("https://merchant.test", ""), ("https://merchant.test", "secret\r\nHeader:x"),
])
def test_requires_explicit_https_and_private_token(url, token):
    with pytest.raises(ValueError):
        MerchantAPIAdapter(url, token)


@pytest.mark.parametrize("operation,result", [("orders.list", {"orders": [order()]}), ("order.get", {"order": order()})])
def test_authenticated_reads_preserve_exact_owned_order(operation, result):
    calls = []
    response = asyncio.run(adapter(result, calls).handle(request(operation)))
    assert response.ok and response.payload == result
    assert calls[0].method == "GET"
    assert calls[0].url.params["customer_ref"] == "customer-1"
    assert calls[0].headers["Authorization"] == "Bearer test-secret"
    assert calls[0].headers["X-QuickFix-Case-Version"] == "4"


@pytest.mark.parametrize("change", [
    {"customer_ref": "other-customer"}, {"data_mode": "simulated"}, {"provider_order_id": None},
    {"provider_order_id": ""}, {"currency": "USD"}, {"order_id": "different-order"},
    {"amount_minor": True}, {"items": []},
])
def test_order_details_fail_closed_for_wrong_owner_or_incomplete_provenance(change):
    result = asyncio.run(adapter({"order": order(**change)}).handle(request()))
    assert not result.ok and result.error_code == "INVALID_MERCHANT_RESPONSE"


def test_empty_real_order_list_stays_empty_without_seeding():
    result = asyncio.run(adapter({"orders": []}).handle(request("orders.list")))
    assert result.ok and result.payload == {"orders": []}


@pytest.mark.parametrize("orders", [[order(), order()], [order(customer_ref="other-customer")]])
def test_list_rejects_duplicates_and_other_customers(orders):
    result = asyncio.run(adapter({"orders": orders}).handle(request("orders.list")))
    assert not result.ok


def test_rejects_order_lists_beyond_coordinator_capacity():
    orders = [order(order_id=f"order-{i}") for i in range(21)]
    result = asyncio.run(adapter({"orders": orders}).handle(request("orders.list")))
    assert not result.ok and result.error_code == "INVALID_MERCHANT_RESPONSE"


def test_eligibility_comes_from_merchant_with_exact_amount_policy_and_expiry():
    body = eligibility()
    calls = []
    result = asyncio.run(adapter(body, calls).handle(request("order.eligibility")))
    assert result.ok and result.payload == body
    assert calls[0].url.path == "/api/orders/merchant-1/cancellation-eligibility"
    assert json.loads(calls[0].content) == {
        "order_id": "merchant-1", "customer_ref": "customer-1", "case_id": "case-1",
        "expected_version": 4, "idempotency_key": "business-1",
    }


@pytest.mark.parametrize("change", [
    {"eligible": "true"}, {"refundable_amount_minor": True}, {"refundable_amount_minor": 12346},
    {"refundable_amount_minor": 0}, {"order": order(order_state="dispatched")},
    {"eligibility_expires_at": "2020-01-01T00:00:00+00:00"},
    {"eligibility_expires_at": "2099-01-01T00:00:00"},
    {"eligibility_expires_at": "2099-01-01T00:00:00+05:30"}, {"policy_version": ""},
    {"eligibility_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=16)).isoformat()},
])
def test_rejects_unsafe_eligibility(change):
    result = asyncio.run(adapter(eligibility(**change)).handle(request("order.eligibility")))
    assert not result.ok and result.error_code == "INVALID_MERCHANT_RESPONSE"


def test_cancel_sends_exact_approval_and_business_key_and_requires_evidence():
    body = cancelled()
    calls = []
    payload = cancellation_payload()
    result = asyncio.run(adapter(body, calls).handle(request("order.cancel", payload)))
    assert result.ok and result.payload == {**body, "cancelled": True}
    assert calls[0].url.path == "/api/orders/merchant-1/cancel"
    assert calls[0].headers["Idempotency-Key"] == "business-1"
    assert json.loads(calls[0].content) == {
        **payload, "customer_ref": "customer-1", "case_id": "case-1",
        "expected_version": 4, "idempotency_key": "business-1",
    }


@pytest.mark.parametrize("change", [
    {"cancellation_ref": ""}, {"refundable_amount_minor": 11999}, {"currency": "USD"},
    {"policy_version": "policy-4"}, {"order": order()}, {"order": order(order_state="cancelled", version=2)},
    {"order": order(order_state="cancelled", version=4)}, {"order": order(order_id="other-order", order_state="cancelled", version=3)},
])
def test_cancel_rejects_missing_evidence_changed_approval_and_wrong_version(change):
    result = asyncio.run(adapter(cancelled(**change)).handle(request("order.cancel", cancellation_payload())))
    assert not result.ok and result.error_code == "INVALID_MERCHANT_RESPONSE"


@pytest.mark.parametrize("payload", [
    cancellation_payload(expected_order_version=True), cancellation_payload(refundable_amount_minor=True),
    cancellation_payload(currency="USD"), cancellation_payload(approval_ref=""),
    cancellation_payload(order_id="../escape"), {**cancellation_payload(), "customer_ref": "attacker"},
    cancellation_payload(eligibility_expires_at="invalid"),
    cancellation_payload(eligibility_expires_at="2099-01-01T00:00:00"),
    cancellation_payload(eligibility_expires_at="2099-01-01T00:00:00+05:30"),
])
def test_invalid_cancel_does_not_call_merchant(payload):
    calls = []
    result = asyncio.run(adapter(cancelled(), calls).handle(request("order.cancel", payload)))
    assert not result.ok and result.error_code == "INVALID_PAYLOAD" and not calls


def test_reconciliation_preserves_original_expired_approval_and_business_key():
    calls = []
    payload = cancellation_payload(eligibility_expires_at="2020-01-01T00:00:00+00:00")
    result = asyncio.run(adapter(cancelled(), calls).handle(request("order.cancel", payload)))
    assert result.ok and result.payload["cancelled"] is True
    assert json.loads(calls[0].content)["eligibility_expires_at"] == payload["eligibility_expires_at"]
    assert calls[0].headers["Idempotency-Key"] == "business-1"


@pytest.mark.parametrize("status,code", [(401, "MERCHANT_UNAUTHORIZED"), (404, "ORDER_NOT_FOUND"),
                                        (409, "MERCHANT_CONFLICT"), (302, "CANCELLATION_OUTCOME_UNKNOWN"),
                                        (500, "CANCELLATION_OUTCOME_UNKNOWN")])
def test_cancellation_http_errors_never_become_success_or_get_retried(status, code):
    calls = []
    result = asyncio.run(adapter(cancelled(), calls, status).handle(request("order.cancel", cancellation_payload())))
    assert not result.ok and result.error_code == code and len(calls) == 1


def test_timeout_after_cancel_is_unknown_and_not_retried():
    calls = []

    def route(req):
        calls.append(req)
        raise httpx.ReadTimeout("response lost")

    bridge = MerchantAPIAdapter("https://merchant.example.invalid", "secret", transport=httpx.MockTransport(route))
    result = asyncio.run(bridge.handle(request("order.cancel", cancellation_payload())))
    assert not result.ok and result.error_code == "CANCELLATION_OUTCOME_UNKNOWN" and len(calls) == 1


def test_rejects_sandbox_requests_and_expired_live_requests_without_network():
    calls = []
    bridge = adapter({}, calls)
    raw = request().dict()
    raw.update(protocol_version="1.0", mode="sandbox")
    with pytest.raises(PeerValidationError):
        asyncio.run(bridge.handle(PeerRequest(**raw)))
    now = datetime.now(timezone.utc)
    result = asyncio.run(bridge.handle(request(created_at=now - timedelta(minutes=6), expires_at=now - timedelta(minutes=1))))
    assert not result.ok and result.error_code == "INVALID_ENVELOPE" and not calls


def test_protocol_has_only_live_signed_handler_and_uses_actual_sender():
    calls, sent = [], []
    protocol = make_merchant_protocol(adapter({"order": order()}, calls), "coordinator-agent")
    assert not protocol.unsigned_message_handlers
    assert set(protocol.signed_message_handlers) == {Model.build_schema_digest(LivePeerRequest)}
    handler = protocol.signed_message_handlers[Model.build_schema_digest(LivePeerRequest)]

    async def send(sender, response):
        sent.append((sender, response))

    ctx = SimpleNamespace(send=send, logger=SimpleNamespace(warning=lambda *_: None))

    async def run():
        await handler(ctx, "attacker", request())
        assert not calls and not sent
        await handler(ctx, "coordinator-agent", request())

    asyncio.run(run())
    assert len(calls) == len(sent) == 1
    assert sent[0][0] == "coordinator-agent" and isinstance(sent[0][1], LivePeerResponse)
