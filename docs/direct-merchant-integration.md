# Direct merchant integration — access pending

The user selected direct integration on 24 October 2026. No pharmacy order system, merchant payment account or email sender is connected. Implementation preparation is complete; external acceptance and real transactions remain pending.

## Three-agent responsibilities

| Component | Responsibility |
| --- | --- |
| QuickFix / OrderFix | ACP conversation, verified customer linking, exact approval, gateway checks, refund reconciliation and notification tracking |
| Med Guardian pharmacy peer | Authenticated merchant order lookup, current cancellation terms and actual cancellation evidence |
| Mail teammate | Authorized sender, separate customer/pharmacy messages and provider acceptance/delivery evidence |

The bridge in `orderfix/commerce/merchant.py` is a contract the merchant must implement, **not an existing pharmacy API**. See the [Med Guardian source review and complete HTTPS contract](medguardian-review.md). The teammate repository and deployment were not modified.

Live peers must implement `LivePeerRequest` and `LivePeerResponse` with version `2.0`, mode `live`, signed sender allowlists and exact request correlation. Sandbox v1 digests remain separate. The mail peer uses the notification operations in the [peer contract](quickfix-contract.md) with the live v2 envelope. Published addresses derived from public seeds must not be trusted as exclusively controlled identities.

## Start the waiting runtime

Preserve the existing private seed and mailbox registration. Stop the older process using that identity, then run:

```text
python -m orderfix.commerce.connected
```

The launcher starts only the coordinator, binds health to loopback, uses separate `data/quickfix-connected/` storage and never starts simulators or legacy sample providers. It does not modify `.env`. Missing configuration returns a connection-required notice in English, Hindi or Kannada, including for old demo commands.

Historical simulations remain explicitly available through `python -m orderfix.commerce.cli demo`. Their chats, screenshots and synthetic amounts are historical evidence only.

## Configuration after merchant authorization

Keep credentials local and outside Git. Do not paste secrets or customer records into chat.

| Setting | Required value |
| --- | --- |
| `QUICKFIX_MERCHANT_REF` | Actual opaque merchant account reference, not a demo label |
| `MEDGUARDIAN_AGENT_ADDRESS` | Verified privately controlled live v2 pharmacy peer |
| `MAIL_AGENT_ADDRESS` | Distinct verified live v2 mail peer |
| `QUICKFIX_GATEWAY=unconfigured` | Real order viewing only; no payment lookup or cancellation/refund |
| `QUICKFIX_GATEWAY=razorpay_live` | Authorized merchant's live gateway account |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` | Private matching live credentials supplied by the merchant |
| `QUICKFIX_ENABLE_LIVE_REFUNDS=false` | Default; retain during read-only acceptance |

The launcher enforces connected peer modes and disables auto-demo. The pharmacy operator separately configures `MERCHANT_API_BASE_URL`, `MERCHANT_API_TOKEN` and `QUICKFIX_COORDINATOR_ADDRESS`. The mail operator supplies its sender credentials.

Use the existing one-use merchant-issued link flow only after the merchant authenticates the customer. A chat-supplied order ID is not proof of ownership. The merchant must enforce the resulting customer scope on every operation.

## Refund conditions and recovery

Orders must include exact purchased items, INR minor-unit totals, authenticated customer, payment/provider-order references, verified recipients and version. Public medicine catalogs cannot supply these facts.

The gateway payment must be captured and match the merchant order's exact total, currency and provider order ID. `merchant_ref` is operator configuration, not independent provider proof; credentials must belong to that merchant.

Merchant terms include an expiring policy version and exact refund amount, including partial refunds. The amount must fit the remaining captured balance. Explicit approval binds the customer, order/version, terms, recipients and integration configuration.

The merchant must atomically cancel the actual order, advance its version and return genuine cancellation evidence with the unchanged approved refund amount and policy. Its durable idempotency journal must replay completed results before checking expiry during recovery; a first attempt after expiry must fail.

Only verified cancellation and an explicitly enabled live-refund switch permit a gateway write. Failures known to precede submission can recover. Unknown submissions retain their operation ID and are reconciled without creating a new refund. Payment, cancellation and mail states remain separate; mail retries cannot repeat a refund.

## Acceptance still required

The automated suite uses injected merchant and gateway responses. It does not verify actual account access, a teammate deployment or external email delivery. First verify private peer identities, merchant customer authentication and read-only real order/payment results. Then arrange an explicitly approved merchant transaction and verify cancellation, refund recovery and both notification recipients.

No actual merchant cancellation, financial transaction or external email has been performed during this preparation. The hackathon's complete real-data shared chat remains pending until authorized services are supplied and verified.
