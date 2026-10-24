# QuickFix payment gateway

> Current real-data mode: direct merchant integration is prepared; pharmacy, payment and mail access are pending. Use `python -m orderfix.commerce.connected`. Historical simulation evidence below does not demonstrate real orders or refunds. See [current integration requirements](direct-merchant-integration.md).

QuickFix retains two development gateway modes alongside `unconfigured` and explicitly gated `razorpay_live`. `simulated` stores local demonstration records in SQLite and never moves money. `razorpay_test` sends real HTTP requests to Razorpay's test environment using a merchant's test credentials. Test mode rejects live keys; live mode requires matching live keys and `allow_live_refunds=True` before mutations. The automated tests use `httpx.MockTransport`; passing them is not evidence of a successful live or sandbox transaction.

This adapter retrieves existing payments and issues refunds. It does **not** charge a customer, create a checkout, register a merchant, approve prescriptions, or provide native ASI checkout. A captured test payment must already exist before testing the Razorpay refund workflow. Razorpay documents separate [Test and Live modes](https://razorpay.com/docs/payments/dashboard/test-live-modes/); do not describe test records as actual customer funds.

## Interface

`orderfix.commerce.payments` exports immutable dataclasses:

- `PaymentSnapshot(id, merchant_ref, status, amount, currency, amount_refunded, order_id=None)`
- `RefundSnapshot(id, payment_id, amount, currency, status, operation_id)`

The constructors are:

```python
RazorpayGateway(key_id, key_secret, merchant_ref, client=None, *,
                mode="razorpay_test", allow_live_refunds=False)
SimulatedGateway(path, merchant_ref="demo-pharmacy")
```

Both expose async `get_payment(payment_id)`, `create_refund(payment_id, amount, currency, operation_id)`, `find_refund(payment_id, operation_id)`, and `get_refund(payment_id, refund_id)`. `find_refund` returns `None` only after a complete bounded scan. `close()` is synchronous; an injected HTTP client's lifetime belongs to its caller.

Only the simulator exposes the synchronous helper:

```python
seed_payment(payment_id, amount, currency="INR", *, status="captured", amount_refunded=0)
```

Amounts are integer smallest currency units: `2500` means ₹25 for INR. No float conversion or currency conversion is performed. IDs must be safe `pay_...` / `rfnd_...` tokens. Operation IDs contain 10–128 letters, digits, hyphens or underscores. Generate an opaque unique operation ID in the application; never put customer names, medicine names, diagnoses, email addresses, or prescription text in it.

Example local simulation:

```python
import asyncio
from orderfix.commerce.payments import SimulatedGateway

async def demo():
    gateway = SimulatedGateway("data/quickfix-demo.sqlite3")
    try:
        gateway.seed_payment("pay_demo001", 10000)
        refund = await gateway.create_refund(
            "pay_demo001", 2500, "INR", "demo-operation-001")
        print("SIMULATION — no money moved:", refund.status)
    finally:
        gateway.close()

asyncio.run(demo())
```

Re-seeding an existing payment preserves its refunds. Reusing the operation ID with the same amount returns the stored refund; changing its amount or payment is rejected. SQLite transactions update the refund record and remaining balance together, including when multiple connections use the same database.

## Authorization and refund lifecycle

The application must bind the customer, order, pharmacy and payment before calling the gateway. `merchant_ref` is a configured account reference, not proof supplied by Razorpay that the customer owns the payment. Never accept a chat-supplied payment ID as sufficient authorization. Keep pharmacy credentials on the server. The application must journal dispatch durably and serialize operations on a payment before making a refund request.

The Razorpay adapter reads the payment, verifies captured state and matching currency, and checks the remaining balance. Razorpay only supports refunds on captured payments. Requests use the documented [normal refund endpoint and idempotency header](https://razorpay.com/docs/api/refunds/normal-refunds-idempotent/), with an explicit amount and only `notes.quickfix_operation` as metadata. Credentials go exclusively to the fixed `https://api.razorpay.com/v1` origin; redirects are not followed.

A successful submission is read back using the [payment-scoped refund endpoint](https://razorpay.com/docs/api/refunds/fetch-specific-refund-payment/). The returned state is preserved: `pending` is not completion, `failed` is not success, and `processed` is a provider state, not a promise that a bank balance is already visible. Status updates can be polled using `get_refund`; this module does not implement webhook ingestion.

`PaymentRejected` means validation or an explicit rejection stopped the operation. `PaymentUnavailable` means a read could not establish state. `PaymentAmbiguous` means a mutation may have happened: retain the original operation ID, reconcile, and show an uncertain/pending outcome. Do not create a new operation to bypass it. Mutation requests are never automatically repeated, including HTTP 409, transport timeouts and server failures. The adapter tries a read-only lookup by operation marker after uncertain submission or readback.

Reads have at most two attempts and a 12-second deadline per request. Reconciliation uses [paginated payment refund history](https://razorpay.com/docs/api/refunds/fetch-multiple-refund-payment/), limited to five pages of 100 records and a 25-second deadline. Truncation, duplicate markers, changing pagination, or malformed responses fail closed. A timeout after dispatch can remain ambiguous even when no matching refund is yet visible; absence is not permission to blindly repeat a dispatched operation.

## Verification

```text
python -m pytest tests/test_commerce_payments.py -q
```

Tests cover captured-state and balance checks, currency/input errors, lost mutation responses, pending state, bounded read retries, refused redirects, readback failures, pagination, idempotency, restart durability, merchant isolation and shared-database balance protection. All provider traffic is mocked; no credentials, accounts, purchases or external refunds are created by the tests.
