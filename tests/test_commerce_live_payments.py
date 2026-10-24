"""Live-mode contracts use fabricated records and MockTransport, never funds."""
import asyncio
import json

import httpx
import pytest

from orderfix.commerce.payments import PaymentRejected, PaymentUnavailable, RazorpayGateway


PAYMENT = "pay_fixture123"
ORDER = "order_fixture123"
OPERATION = "quickfix_live_fixture123"


class Provider:
    def __init__(self):
        self.payment = dict(id=PAYMENT, entity="payment", order_id=ORDER,
                            status="captured", captured=True, amount=108900,
                            currency="INR", amount_refunded=0)
        self.refunds = []
        self.calls = []
        self.timeout_after_commit = False

    def handle(self, request):
        assert request.url.host == "api.razorpay.com"
        assert request.url.scheme == "https"
        self.calls.append(request)
        if request.method == "POST":
            assert request.headers["X-Refund-Idempotency"] == OPERATION
            body = json.loads(request.content)
            result = dict(id="rfnd_fixture123", entity="refund", payment_id=PAYMENT,
                          amount=body["amount"], currency="INR", status="processed",
                          notes=body["notes"])
            self.refunds.append(result)
            if self.timeout_after_commit:
                raise httpx.ReadTimeout("private provider text", request=request)
            return httpx.Response(200, json=result)
        if request.url.path.endswith("/refunds"):
            return httpx.Response(200, json=dict(entity="collection", count=len(self.refunds),
                                                items=self.refunds))
        if "/refunds/" in request.url.path:
            return httpx.Response(200, json=self.refunds[0])
        return httpx.Response(200, json=self.payment)


def run_provider(check, *, enabled=False):
    async def run():
        provider = Provider()
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handle)) as client:
            gateway = RazorpayGateway("rzp_live_fixture", "fixture-secret", "merchant-reference",
                                      client, mode="razorpay_live", allow_live_refunds=enabled)
            await check(gateway, provider)
    asyncio.run(run())


def test_live_reads_work_while_refund_submission_is_disabled():
    async def check(gateway, provider):
        result = await gateway.get_payment(PAYMENT)
        assert gateway.mode == "razorpay_live"
        assert gateway.live_refunds_enabled is False
        assert result.order_id == ORDER
        assert result.amount == 108900
        # This is the configured mapping, not a provider-returned merchant ID.
        assert result.merchant_ref == "merchant-reference"
        assert not hasattr(result, "email")
        provider.calls.clear()
        with pytest.raises(PaymentRejected, match="Live refunds are disabled"):
            await gateway.create_refund(PAYMENT, 4200, "INR", OPERATION)
        assert provider.calls == []
        assert provider.refunds == []
    run_provider(check)


def test_live_disabled_transport_cannot_submit_a_mutation():
    async def check(gateway, provider):
        with pytest.raises(PaymentRejected, match="Live refunds are disabled"):
            await gateway._request("POST", f"/payments/{PAYMENT}/refund", json={"amount": 4200})
        assert provider.calls == []
    run_provider(check)


@pytest.mark.parametrize("mode,key", [
    ("razorpay_live", "rzp_test_fixture"),
    ("razorpay_test", "rzp_live_fixture"),
    ("razorpay_live", "rzp_live_"),
    ("razorpay_live", "rzp_live_fixture\n"),
    ("live", "rzp_live_fixture"),
])
def test_mode_and_credential_mismatches_are_rejected(mode, key):
    with pytest.raises(PaymentRejected):
        RazorpayGateway(key, "fixture-secret", "merchant-reference", mode=mode)


@pytest.mark.parametrize("enabled", ["true", "false", 1, None])
def test_live_switch_requires_an_actual_boolean(enabled):
    with pytest.raises(PaymentRejected, match="boolean"):
        RazorpayGateway("rzp_live_fixture", "fixture-secret", "merchant-reference",
                        mode="razorpay_live", allow_live_refunds=enabled)


def test_live_write_enable_change_invalidates_existing_account_binding():
    read_only = RazorpayGateway("rzp_live_fixture", "fixture-secret", "merchant-reference",
                               mode="razorpay_live")
    enabled = RazorpayGateway("rzp_live_fixture", "fixture-secret", "merchant-reference",
                             mode="razorpay_live", allow_live_refunds=True)
    changed_account = RazorpayGateway("rzp_live_second", "fixture-secret", "merchant-reference",
                                     mode="razorpay_live", allow_live_refunds=True)
    assert enabled.live_refunds_enabled is True
    assert len({read_only.account_fingerprint, enabled.account_fingerprint,
                changed_account.account_fingerprint}) == 3
    assert "fixture" not in enabled.account_fingerprint


@pytest.mark.parametrize("order_id", [None, "", "pay_wrong", "order_", "order_bad value", 123])
def test_live_payment_without_valid_provider_order_is_unavailable(order_id):
    async def check(gateway, provider):
        provider.payment["order_id"] = order_id
        with pytest.raises(PaymentUnavailable, match="inconsistent payment data"):
            await gateway.get_payment(PAYMENT)
        with pytest.raises(PaymentUnavailable):
            await gateway.create_refund(PAYMENT, 4200, "INR", OPERATION)
        assert all(request.method == "GET" for request in provider.calls)
        assert provider.refunds == []
    run_provider(check, enabled=True)


def test_live_payment_with_omitted_provider_order_is_unavailable():
    async def check(gateway, provider):
        del provider.payment["order_id"]
        with pytest.raises(PaymentUnavailable):
            await gateway.get_payment(PAYMENT)
    run_provider(check)


def test_live_payment_response_cannot_substitute_another_payment():
    async def check(gateway, provider):
        provider.payment["id"] = "pay_someoneElse"
        with pytest.raises(PaymentUnavailable):
            await gateway.create_refund(PAYMENT, 4200, "INR", OPERATION)
        assert provider.refunds == []
    run_provider(check, enabled=True)


@pytest.mark.parametrize("operation", ["payment", "refund", "find", "create"])
def test_synthetic_payment_ids_cannot_reach_live_provider(operation):
    async def check(gateway, provider):
        with pytest.raises(PaymentRejected, match="Synthetic"):
            if operation == "payment":
                await gateway.get_payment("pay_sim_customer1")
            elif operation == "refund":
                await gateway.get_refund("pay_sim_customer1", "rfnd_fixture123")
            elif operation == "find":
                await gateway.find_refund("pay_sim_customer1", OPERATION)
            else:
                await gateway.create_refund("pay_sim_customer1", 4200, "INR", OPERATION)
        assert provider.calls == []
    run_provider(check, enabled=True)


@pytest.mark.parametrize("timeout_after_commit", [False, True])
def test_enabled_live_refund_preserves_exact_amount_idempotency_and_readback(timeout_after_commit):
    async def check(gateway, provider):
        provider.timeout_after_commit = timeout_after_commit
        result = await gateway.create_refund(PAYMENT, 4200, "INR", OPERATION)
        assert (result.amount, result.payment_id, result.operation_id, result.status) == (
            4200, PAYMENT, OPERATION, "processed")
        duplicate = await gateway.create_refund(PAYMENT, 4200, "INR", OPERATION)
        assert duplicate == result
        posts = [request for request in provider.calls if request.method == "POST"]
        assert len(posts) == 1
        assert json.loads(posts[0].content) == {
            "amount": 4200, "speed": "normal", "notes": {"quickfix_operation": OPERATION}}
        with pytest.raises(PaymentRejected, match="different refund request"):
            await gateway.create_refund(PAYMENT, 4201, "INR", OPERATION)
        assert len(provider.refunds) == 1
    run_provider(check, enabled=True)


@pytest.mark.parametrize("order_id", [None, ORDER])
def test_test_mode_retains_optional_provider_order_compatibility(order_id):
    async def run():
        provider = Provider()
        provider.payment["order_id"] = order_id
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handle)) as client:
            gateway = RazorpayGateway("rzp_test_fixture", "fixture-secret", "merchant-reference", client)
            assert gateway.mode == "razorpay_test"
            assert gateway.live_refunds_enabled is False
            assert (await gateway.get_payment(PAYMENT)).order_id == order_id
    asyncio.run(run())
