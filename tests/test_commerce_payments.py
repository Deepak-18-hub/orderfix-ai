"""No external requests or funds: HTTP contracts use MockTransport only."""
import asyncio
import json

import httpx
import pytest

from orderfix.commerce.payments import (
    PaymentAmbiguous, PaymentRejected, PaymentUnavailable,
    RazorpayGateway, SimulatedGateway,
)

PAYMENT = "pay_test123"
OPERATION = "quickfix_operation_123"


def payment(**changes):
    return dict(id=PAYMENT, entity="payment", status="captured", captured=True,
                amount=10000, currency="INR", amount_refunded=0, **changes)


def refund(**changes):
    data = dict(id="rfnd_test123", entity="refund", payment_id=PAYMENT,
                amount=2500, currency="INR", status="pending",
                notes={"quickfix_operation": OPERATION})
    data.update(changes)
    return data


class Provider:
    def __init__(self):
        self.payment = payment()
        self.refunds = []
        self.posts = []
        self.calls = []
        self.fault = None

    def handle(self, request):
        assert request.url.scheme == "https"
        assert request.url.host == "api.razorpay.com"
        assert request.headers["authorization"].startswith("Basic ")
        self.calls.append(request)
        if self.fault:
            response = self.fault(request)
            if response is not None:
                return response
        if request.method == "POST":
            payload = json.loads(request.content)
            self.posts.append(payload)
            assert request.headers["X-Refund-Idempotency"] == OPERATION
            created = refund(amount=payload["amount"], notes=payload["notes"])
            self.refunds.append(created)
            return httpx.Response(200, json=created)
        if request.url.path.endswith("/refunds"):
            skip = int(request.url.params["skip"])
            count = int(request.url.params["count"])
            items = self.refunds[skip:skip + count]
            return httpx.Response(200, json={"entity": "collection", "count": len(items), "items": items})
        if "/refunds/" in request.url.path:
            target = request.url.path.rsplit("/", 1)[-1]
            match = next(item for item in self.refunds if item["id"] == target)
            return httpx.Response(200, json=match)
        return httpx.Response(200, json=self.payment)


def run_provider(check):
    async def run():
        provider = Provider()
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider.handle)) as client:
            gateway = RazorpayGateway("rzp_test_example", "secret-for-tests", "demo-pharmacy", client)
            await check(gateway, provider, client)
    asyncio.run(run())


@pytest.mark.parametrize("key", ["rzp_live_example", "", "rzp_test_", "rzp_test_a\n"])
def test_reject_live_or_malformed_credentials(key):
    with pytest.raises(PaymentRejected, match="TEST credentials"):
        RazorpayGateway(key, "secret-not-to-leak", "pharmacy")


def test_success_is_read_back_and_only_operation_metadata_is_sent():
    async def check(gateway, provider, client):
        actual = await gateway.create_refund(PAYMENT, 2500, "INR", OPERATION)
        assert actual.status == "pending"
        assert actual.operation_id == OPERATION
        assert provider.posts == [{"amount": 2500, "speed": "normal", "notes": {"quickfix_operation": OPERATION}}]
        assert provider.calls[-1].url.path.endswith("/refunds/rfnd_test123")
        snapshot = await gateway.get_payment(PAYMENT)
        assert snapshot.merchant_ref == "demo-pharmacy"
        assert not hasattr(snapshot, "email")
        gateway.close()
        assert not client.is_closed
    run_provider(check)


@pytest.mark.parametrize("change,amount,currency", [
    ({"status": "authorized", "captured": False}, 2500, "INR"),
    ({"amount_refunded": 9000}, 2500, "INR"),
    ({}, 2500, "USD"),
    ({"status": "refunded", "amount_refunded": 10000}, 2500, "INR"),
])
def test_refund_preconditions_prevent_mutation(change, amount, currency):
    async def check(gateway, provider, _):
        provider.payment.update(change)
        with pytest.raises(PaymentRejected):
            await gateway.create_refund(PAYMENT, amount, currency, OPERATION)
        assert not provider.posts
    run_provider(check)


@pytest.mark.parametrize("amount,currency,operation", [
    (True, "INR", OPERATION), (0, "INR", OPERATION), (1.5, "INR", OPERATION),
    (100, "inr", OPERATION), (100, "INR", "short"), (100, "INR", "contains space"),
    (101, "KWD", OPERATION),
])
def test_invalid_inputs_make_no_requests(amount, currency, operation):
    async def check(gateway, provider, _):
        with pytest.raises(PaymentRejected):
            await gateway.create_refund(PAYMENT, amount, currency, operation)
        assert not provider.calls
    run_provider(check)


def test_repeated_operation_returns_existing_even_after_full_refund():
    async def check(gateway, provider, _):
        provider.refunds = [refund(amount=10000, status="processed")]
        provider.payment.update(status="refunded", amount_refunded=10000)
        found = await gateway.create_refund(PAYMENT, 10000, "INR", OPERATION)
        assert found.status == "processed"
        assert not provider.posts
        with pytest.raises(PaymentRejected, match="different refund"):
            await gateway.create_refund(PAYMENT, 5000, "INR", OPERATION)
    run_provider(check)


def test_timeout_after_provider_commit_reconciles_without_second_post():
    async def check(gateway, provider, _):
        mutations = []
        def fault(request):
            if request.method == "POST":
                mutations.append(request)
                provider.refunds.append(refund(status="processed"))
                raise httpx.ReadTimeout("secret-for-tests and health details", request=request)
        provider.fault = fault
        result = await gateway.create_refund(PAYMENT, 2500, "INR", OPERATION)
        assert result.status == "processed"
        assert len(mutations) == 1
    run_provider(check)


@pytest.mark.parametrize("status", [409, 429, 500])
def test_uncertain_http_outcome_is_ambiguous_not_retried(status):
    async def check(gateway, provider, _):
        provider.fault = lambda request: httpx.Response(status, text="secret-for-tests") if request.method == "POST" else None
        with pytest.raises(PaymentAmbiguous) as error:
            await gateway.create_refund(PAYMENT, 2500, "INR", OPERATION)
        assert "secret" not in str(error.value)
        assert sum(r.method == "POST" for r in provider.calls) == 1
    run_provider(check)


def test_known_provider_rejection_is_sanitized():
    async def check(gateway, provider, _):
        provider.fault = lambda request: httpx.Response(400, json={"error": "secret-for-tests"}) if request.method == "POST" else None
        with pytest.raises(PaymentRejected) as error:
            await gateway.create_refund(PAYMENT, 2500, "INR", OPERATION)
        assert "secret" not in str(error.value)
        assert sum(r.method == "POST" for r in provider.calls) == 1
    run_provider(check)


def test_readback_failure_after_submission_stays_ambiguous():
    async def check(gateway, provider, _):
        def fault(request):
            if request.method == "GET" and provider.posts:
                return httpx.Response(503, text="private")
        provider.fault = fault
        with pytest.raises(PaymentAmbiguous):
            await gateway.create_refund(PAYMENT, 2500, "INR", OPERATION)
        assert len(provider.posts) == 1
    run_provider(check)


def test_malformed_success_cannot_be_reported_as_refund():
    async def check(gateway, provider, _):
        provider.fault = lambda request: httpx.Response(200, json={}) if request.method == "POST" else None
        with pytest.raises(PaymentAmbiguous):
            await gateway.create_refund(PAYMENT, 2500, "INR", OPERATION)
    run_provider(check)


def test_transient_read_retries_once_without_leaking_error():
    async def check(gateway, provider, _):
        def fault(request):
            raise httpx.ConnectError("secret-for-tests", request=request)
        provider.fault = fault
        with pytest.raises(PaymentUnavailable) as error:
            await gateway.get_payment(PAYMENT)
        assert "secret" not in str(error.value)
        assert len(provider.calls) == 2
    run_provider(check)


def test_redirect_is_not_followed_with_credentials():
    async def check(gateway, provider, _):
        provider.fault = lambda _: httpx.Response(302, headers={"Location": "https://untrusted.invalid/"})
        with pytest.raises(PaymentUnavailable):
            await gateway.get_payment(PAYMENT)
        assert len(provider.calls) == 1
    run_provider(check)


def test_pagination_finds_second_page_and_rejects_truncation():
    async def check(gateway, provider, _):
        provider.refunds = [refund(id=f"rfnd_other{i}", notes={}) for i in range(100)] + [refund()]
        result = await gateway.find_refund(PAYMENT, OPERATION)
        assert result.operation_id == OPERATION
        assert [r.url.params["skip"] for r in provider.calls] == ["0", "100"]
        provider.refunds = [refund(id=f"rfnd_other{i}", notes={}) for i in range(500)]
        with pytest.raises(PaymentUnavailable, match="scan limit"):
            await gateway.find_refund(PAYMENT, OPERATION)
    run_provider(check)


def test_duplicate_operation_and_wrong_payment_are_not_accepted():
    async def check(gateway, provider, _):
        provider.refunds = [refund(), refund(id="rfnd_second")]
        with pytest.raises(PaymentUnavailable, match="Multiple refunds"):
            await gateway.find_refund(PAYMENT, OPERATION)
        provider.refunds = [refund(payment_id="pay_other")]
        with pytest.raises(PaymentUnavailable):
            await gateway.find_refund(PAYMENT, OPERATION)
    run_provider(check)


def test_inconsistent_payment_is_not_refundable():
    async def check(gateway, provider, _):
        provider.payment["captured"] = False
        with pytest.raises(PaymentUnavailable):
            await gateway.create_refund(PAYMENT, 2500, "INR", OPERATION)
        assert not provider.posts
    run_provider(check)


def test_simulation_persists_idempotency_and_balance_after_restart(tmp_path):
    path = tmp_path / "payments.sqlite3"
    gateway = SimulatedGateway(path)
    gateway.seed_payment(PAYMENT, 10000)
    first = asyncio.run(gateway.create_refund(PAYMENT, 2500, "INR", OPERATION))
    gateway.close()
    gateway = SimulatedGateway(path)
    assert gateway.mode == "simulated"
    assert gateway.seed_payment(PAYMENT, 10000).amount_refunded == 2500
    assert asyncio.run(gateway.create_refund(PAYMENT, 2500, "INR", OPERATION)) == first
    assert asyncio.run(gateway.find_refund(PAYMENT, OPERATION)) == first
    assert asyncio.run(gateway.get_refund(PAYMENT, first.id)) == first
    with pytest.raises(PaymentRejected):
        asyncio.run(gateway.create_refund(PAYMENT, 2501, "INR", OPERATION))
    with pytest.raises(PaymentRejected):
        asyncio.run(gateway.create_refund(PAYMENT, 7501, "INR", OPERATION + "2"))
    assert asyncio.run(gateway.get_payment(PAYMENT)).amount_refunded == 2500
    gateway.close()


def test_simulation_merchant_isolation_and_operation_binding(tmp_path):
    path = tmp_path / "payments.sqlite3"
    first = SimulatedGateway(path, "pharmacy-a")
    second = SimulatedGateway(path, "pharmacy-b")
    first.seed_payment(PAYMENT, 1000)
    first.seed_payment("pay_other", 1000)
    asyncio.run(first.create_refund(PAYMENT, 1000, "INR", OPERATION))
    assert asyncio.run(first.get_payment(PAYMENT)).status == "refunded"
    with pytest.raises(PaymentRejected):
        asyncio.run(second.get_payment(PAYMENT))
    with pytest.raises(PaymentRejected):
        asyncio.run(first.create_refund("pay_other", 1000, "INR", OPERATION))
    assert asyncio.run(first.get_payment("pay_other")).amount_refunded == 0
    first.close()
    second.close()


def test_two_simulation_connections_cannot_exceed_shared_balance(tmp_path):
    path = tmp_path / "payments.sqlite3"
    first, second = SimulatedGateway(path), SimulatedGateway(path)
    first.seed_payment(PAYMENT, 10000)
    asyncio.run(first.create_refund(PAYMENT, 7500, "INR", OPERATION))
    with pytest.raises(PaymentRejected):
        asyncio.run(second.create_refund(PAYMENT, 7500, "INR", OPERATION + "2"))
    assert asyncio.run(second.get_payment(PAYMENT)).amount_refunded == 7500
    first.close()
    second.close()
