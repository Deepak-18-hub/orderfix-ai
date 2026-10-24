# QuickFix commerce peer contract v1.0

This contract connects the OrderFix/QuickFix commerce coordinator to a fictional
pharmacy simulator and a captured-email peer. It is a **sandbox** contract. It
does not authorize real medicine dispensing, money movement, or email delivery.

QuickFix owns the user-facing ACP conversation, commerce cases, cancellation and
refund coordination, payment adapters and notification evidence. Med Guardian
provides authenticated order context and pharmacy decisions. Medication
adherence records stay with Med Guardian. Medication supply must increase only
after a verified delivery/receipt event, never when payment is approved.

## Transport and ownership

`orderfix/commerce/protocol.py` defines `PeerRequest` and `PeerResponse` as
`uagents.Model` classes compatible with the pinned uAgents runtime. These use
Pydantic v1 internally. Serialize with `.json()` and parse with `.parse_raw()`.

`make_peer_protocol(on_response, allowed_senders)` installs a signed response
handler. The callback is `on_response(ctx, actual_sender, response)` and may be
async. Before updating a case, the callback must load the pending request and
call `validate_response(response, pending_request, actual_sender, expected_peers)`.
Only the expected peer for that operation should be allowed by that final check.
The factory's sender/freshness check does not replace pending-request matching.

`make_pharmacy_protocol(service, allowed_sender)` and
`make_mail_protocol(service, allowed_sender)` install signed request handlers.
They accept only the actual configured coordinator transport address and return
results through `ctx.send`. No payload field can nominate another sender. Empty
allowlists reject every message. The framework verifies signatures because these
handlers use `allow_unverified=False`.

The services expose synchronous `handle(request) -> PeerResponse` for focused
unit tests. That method is not a public/authenticated endpoint; deployed access
must pass through the signed handlers. These factories do not create accounts,
register agents publicly or start network listeners.

## Envelope

Every request and response contains these same fields:

| Field | Constraint |
| --- | --- |
| `protocol_version` | Exactly `1.0` |
| `request_id` | Canonical UUID string identifying the transport request |
| `correlation_id` | Opaque coordinator correlation identifier |
| `case_id` | Opaque commerce case identifier |
| `customer_ref` | Opaque, authenticated customer scope; no email or patient details |
| `operation` | One of the six operations below |
| `idempotency_key` | Stable identifier for one intended operation |
| `created_at`, `expires_at` | Timezone-aware UTC; positive lifetime at most 15 minutes |
| `expected_version` | Nonnegative integer for the **coordinator case**, not the pharmacy order |
| `mode` | Exactly `sandbox` |
| `payload` | JSON object, maximum 32 KiB |

References use 1–128 ASCII letters, digits, `_`, `.`, `:`, or `-`, beginning with
a letter or digit. A message more than 30 seconds in the future or at/past its
expiry is rejected. Unknown fields, unsupported versions and live mode fail
validation. Responses copy every envelope field exactly and add `ok: bool` and
`error_code: string | null`. Failed responses need an uppercase bounded error
code; successful responses cannot have one. Payloads are validated by each peer.

Never forward all customers as ordinary ACP text under the coordinator's one
agent address. The typed customer scope is assigned by the coordinator after
authentication; it is not accepted as shopper identity from free-form chat.

## Pharmacy operations

`PharmacyService(path, auto_seed=False)` persists orders and operation results in
SQLite. No real pharmacy, prescription service, inventory feed or merchant API is
connected. Only explicitly simulated orders can be seeded.

| Operation | Request payload | Successful response payload |
| --- | --- | --- |
| `orders.list` | `{}` | `{"orders": [Order, ...]}` |
| `order.get` | `{"order_id": "..."}` | `{"order": Order}` |
| `order.eligibility` | `{"order_id": "..."}` | `{"eligible": bool, "order": Order, "policy_text": "..."}` |
| `order.cancel` | `{"order_id": "...", "expected_order_version": int, "approval_ref": "..."}` | `{"order": Order, "cancelled": true}` |

An `Order` has `order_id`, `customer_ref`, `title`, `pharmacy_ref`,
`pharmacy_name`, `merchant_ref`, `payment_id`, `amount_minor`, `currency`,
`order_state`, `version`, `customer_email`, `pharmacy_email`, `items` and
`data_mode`. Currency is `INR`. State is `accepted`, `dispatched` or `cancelled`.
Items contain `name`, `strength`, `form` and positive integer `quantity`.

Order lookup is scoped by both customer and order ID. Cancellation is permitted
only from `accepted` with the exact current pharmacy order version. The version
in the payload is separate from the coordinator's envelope `expected_version`.
The state update, version increment and idempotency result commit in one SQLite
transaction. Two racing cancellations cannot both apply.

`approval_ref` is an opaque reference to the coordinator's already verified
shopper approval. The simulator does not independently authenticate a shopper or
interpret natural language as authorization. The coordinator must bind approval
to the customer, case, order, amount, version and expiry before sending it.

The sample policy permits cancellation before dispatch. Dispatched medicine
orders require pharmacy review; this simulator does not accept automatic returns,
restock medicine, change treatment or dispense a replacement.

### Explicit fixtures

`seed_order(order_dict)` inserts one simulated order.
`seed_customer(customer_ref, payment_ids=None)` creates two fictional orders once:

- IDs: `demo-{customer_ref}-1` and `demo-{customer_ref}-2`.
- Payment IDs: `pay_sim_{sha256(customer_ref UTF-8).hexdigest()[:16]}1` and the same
  prefix ending in `2`. Two explicit payment IDs can be supplied instead.
- First order: 34,900 paise, accepted, version 1.
- Second order: 19,900 paise, dispatched, version 1.
- Merchant/pharmacy reference: `demo-pharmacy`; fictional display name:
  `Demo Willow Pharmacy (fictional)`.
- Mail addresses: `patient@example.invalid`, `pharmacy@example.invalid`.
- Medicine attributes explicitly identify synthetic, unusable demo items.

With the explicit simulator-only `auto_seed=True` option, an authenticated
coordinator `orders.list` request seeds its customer only when `customer_ref`
begins with `demo-`. Other customer references are not automatically seeded.
Fixtures never reset an existing cancelled order after process restart.

## Captured-email operations

`MailService(path)` persists captured messages. It has no SMTP or email-provider
adapter and never claims that a message reached an inbox.

`mail.send` accepts exactly:

```json
{
  "event_id": "event-opaque-id",
  "recipient_role": "customer",
  "recipient": "patient@example.invalid",
  "template_version": "1",
  "language": "en",
  "order": {"title": "Synthetic order", "pharmacy_name": "Fictional pharmacy"},
  "refund": {"amount_minor": 34900, "currency": "INR", "status": "pending", "refund_id": null},
  "order_state": "cancelled"
}
```

The role is `customer` or `pharmacy`; languages are `en`, `hi`, `kn`.
`refund_id` may be omitted. Templates preserve the supplied refund status rather
than treating pending/failed refunds as successful. The subject and body state
that the email is locally captured and has not been sent. No medication dosing
instructions or unnecessary patient profile are added.

`mail.status` takes `{"notification_id": "captured_..."}` and checks customer
ownership. Both operations return:

```json
{
  "notification_id": "captured_...",
  "state": "captured",
  "provider_message_id": null,
  "evidence_mode": "captured"
}
```

The unique `(event_id, recipient_role)` record prevents duplicate captures even
if a caller mistakenly supplies a new idempotency key. A changed recipient,
customer or content under that event/role is rejected. Captured recipient and
body data remain in the configured local SQLite file; use synthetic recipients
for demonstrations.

## Retry and failure rules

The idempotency fingerprint binds version, case, customer, operation, coordinator
version, mode and payload. A transport retry can have a fresh request UUID and
deadline, but cannot change those business fields. It receives the original
operation outcome with its current envelope echoed. Copying a key onto another
customer or altered payload produces `IDEMPOTENCY_CONFLICT` without exposing the
original result. Reconciliation queries should use new query keys when fresh
state is required.

The coordinator renews an expired unresolved request with a new transport UUID
and expiry while preserving the complete business payload and idempotency key.
The peer must retain the effect result across restarts. Transient failures
`UNAVAILABLE`, `TEMPORARY_FAILURE`, `RATE_LIMITED`, and `PROVIDER_UNAVAILABLE`
are retryable; do not cache them as permanent completed effects. Permanent
rejections and actual completed effects must remain deduplicated.

For connected mail, return a stable `notification_id` for every successful
response. `accepted` and `delivered` additionally require `provider_message_id`.
QuickFix polls `mail.status` after `queued` or `accepted`, with a fresh read key
every 30 seconds. Echo the same notification ID. The mail agent owns delivery
attempts; a permanent `failed` or `bounced` result requires operator attention.
No mail response can change payment or pharmacy state.

## Coordinator-owned refund and case contracts

Refund authority stays inside QuickFix. A mail or pharmacy peer cannot invoke a
refund using an arbitrary payload. `CommerceCase` in `models.py` is the canonical
persisted case contract: immutable order/customer facts, approval reference,
approved minor-unit amount/currency, cancellation key, refund key, provider
reference, independent financial/notification states and integration binding.

The typed gateway request is
`create_refund(payment_id: str, amount: int, currency: str, operation_id: str)`;
the result is the immutable `RefundSnapshot(id, payment_id, amount, currency,
status, operation_id)` from `payments.py`. Only a verified cancelled order with
the current case-bound approval can dispatch it. QuickFix reads status using
`get_refund` or `find_refund`, then sends the corresponding versioned
`mail.send` event. Teammates never supply payment proof by acknowledgement.

ACP case-status requests are an exact `qf_status` card action or a natural status
question in the authenticated conversation. The result view separately reports
order, payment, refund, customer notification and pharmacy notification states.
This version has no peer-initiated refund or cross-customer case-status endpoint.

Error codes include `INVALID_ENVELOPE`, `INVALID_PAYLOAD`,
`UNSUPPORTED_OPERATION`, `ORDER_NOT_FOUND`, `VERSION_CONFLICT`, `NOT_ELIGIBLE`,
`IDEMPOTENCY_CONFLICT`, `NOTIFICATION_NOT_FOUND`, `EVENT_CONFLICT`, `STORAGE_ERROR`.
Unauthenticated/unallowlisted network senders receive no reply and cause no
service mutation. Storage failures roll back the transaction. A send timeout is
not proof that the peer action failed; reconcile or repeat the identical keyed
operation instead of creating a new action.

## Future operations are not implemented

Quotes, pharmacy checkout, prescription review, stock reservations, shipment
events, delivery confirmations, ASI buyer-payment relay, payment settlement and
real email delivery need separately versioned adapters and contracts. Do not
advertise them as supported operations of this protocol. Real pharmacy policy
and professional review must control dispensing and medicine-return decisions.

Focused protocol tests mock `Context.send`. The separate runnable CLI demo uses
actual uAgents local dispatch among distinct agent identities; its independent
HTTP loopback probe verifies a valid signature and rejects a tampered envelope.
Neither result establishes a connection to a deployed teammate agent.
