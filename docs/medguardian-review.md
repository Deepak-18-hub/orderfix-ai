# Med Guardian integration review

Read-only source review of [sujanshetty707/med-guardian at
4bad779a7e857ae0d98b43610da4dc74dd162acc](https://github.com/sujanshetty707/med-guardian/tree/4bad779a7e857ae0d98b43610da4dc74dd162acc).
This records observed code at that commit; it does not verify a deployed service
or claim the upstream project has applied these changes. No upstream repository
was modified and its code was not executed during review.

The latest changes since the earlier reviewed `0fc48c9` commit affect interactive
cards and conversation routing. They do not add a merchant order source,
cancellation API or payment gateway. Source downloaded for review is not imported
or executed by QuickFix.

The public GitHub API was checked again on 24 October 2026: `main` pointed
to this commit (committed at 07:49:46 UTC). Its README publishes the following
addresses; this review does **not** verify their current operator, registration,
availability or compatibility with the QuickFix contract:

- Guardian Orchestrator: `agent1q09642fqjyuhjrcpupdwcskl7n3890zw6y6py3n8ugcfjvw8cayr6zcd0lx`
- Pharmacy specialist: `agent1q0xte8vak3zx99s5d9sm6qp0s9jkrf6a4kul7598uuwt03efax4fgn43sgn`

Source: [pinned registration section](https://github.com/sujanshetty707/med-guardian/blob/4bad779a7e857ae0d98b43610da4dc74dd162acc/README.md#6-agentverse-registration).
`MEDGUARDIAN_AGENT_ADDRESS` must identify the actual peer implementing the
commerce contract; do not insert a published ACP-only address and assume that it
can answer typed pharmacy requests.

## Reusable implementation

The project supplies a Python/uAgents ACP orchestrator, typed specialist-message
models, SQLAlchemy persistence, medication parsing, RxNorm/openFDA wrappers and
Telegram notification code. Its [Bureau launcher](https://github.com/sujanshetty707/med-guardian/blob/4bad779a7e857ae0d98b43610da4dc74dd162acc/scripts/run_agents.py)
creates five agents. The ordinary [orchestrator conversation path](https://github.com/sujanshetty707/med-guardian/blob/4bad779a7e857ae0d98b43610da4dc74dd162acc/agents/orchestrator/agent.py)
calls tools and the shared database directly rather than delegating its workflow
to those specialist agents. Therefore a new integration must demonstrate actual
messages and responses, not infer collaboration from the five instances.

## Transaction and identity blockers

| Observation at the pinned commit | Required integration boundary |
| --- | --- |
| [PaymentManager](https://github.com/sujanshetty707/med-guardian/blob/4bad779a7e857ae0d98b43610da4dc74dd162acc/tools/payments.py) creates a dictionary/random `pay_` reference; verification always returns true. | Treat it as a stub. Real payment status must come from an authenticated adapter and verified settlement evidence. |
| [Approval parsing](https://github.com/sujanshetty707/med-guardian/blob/4bad779a7e857ae0d98b43610da4dc74dd162acc/core/llm.py) tests substrings such as approve/pay before cancellation; a negated approval can match. | Require exact, single-use, expiring approval tied to customer, case, amount and version. Never authorize through LLM classification. |
| [Refill approval](https://github.com/sujanshetty707/med-guardian/blob/4bad779a7e857ae0d98b43610da4dc74dd162acc/agents/orchestrator/agent.py) immediately adds 30 units and reports an authorized order without calling a payment gateway or persisting the specialist order. | Keep payment, pharmacy acceptance, shipment and receipt separate. Increase home stock only after receipt is verified. |
| [SessionManager](https://github.com/sujanshetty707/med-guardian/blob/4bad779a7e857ae0d98b43610da4dc74dd162acc/core/acp.py) keys state only by sender; the orchestrator derives patient IDs from a truncated sender prefix. | A bridge sending all users from one agent address would mix state. Use full authenticated user scope plus explicit conversation/case correlation. |
| [Specialist models](https://github.com/sujanshetty707/med-guardian/blob/4bad779a7e857ae0d98b43610da4dc74dd162acc/core/models.py) carry patient IDs, but handlers do not establish caller ownership. | Allowlist the signed actual sender and bind opaque customer scope to verified consent at the coordinator. |
| [Card metadata](https://github.com/sujanshetty707/med-guardian/blob/4bad779a7e857ae0d98b43610da4dc74dd162acc/core/cards.py) is custom; incoming ACP extraction reads text only. | Verify real ASI callback/text delivery and retain exact text confirmation fallback; do not assume metadata makes a functioning native card. |

The configured default seeds and deployment seeds are public in the repository.
Production agent identities need private stable seeds and fresh allowlist entries;
an address derived from a published seed is not an exclusive identity.

## Data and operational limits

- [Mock pharmacy quotes](https://github.com/sujanshetty707/med-guardian/blob/4bad779a7e857ae0d98b43610da4dc74dd162acc/tools/pricing/mock_partner.py)
  use hardcoded prices and real chain names with fabricated pickup promises.
  `estimated=False` does not make them merchant quotes. Use fictional pharmacy
  names and an explicit simulation marker until a permitted merchant feed exists.
- [NADAC pricing](https://github.com/sujanshetty707/med-guardian/blob/4bad779a7e857ae0d98b43610da4dc74dd162acc/tools/pricing/nadac.py)
  is a hardcoded unit-cost table, not a live CMS lookup or dated benchmark import.
- [The pharmacy agent](https://github.com/sujanshetty707/med-guardian/blob/4bad779a7e857ae0d98b43610da4dc74dd162acc/agents/pharmacy/agent.py)
  can save a local order after unconditional payment verification, but does not
  contact a merchant, check stock, validate a prescription or book delivery.
- [Notifications](https://github.com/sujanshetty707/med-guardian/blob/4bad779a7e857ae0d98b43610da4dc74dd162acc/tools/notify/dispatcher.py)
  implement a Telegram branch; unsupported channels can return true without
  delivery. No order-email or refund adapter exists in the reviewed tree.
- The normal schedule-confirmation path creates a demo dose event rather than a
  persisted recurring Schedule. The schedule agent's source does not register a
  recurring reminder worker. Reminder, caregiver-consent and weekly-summary
  claims require independent verification before they become integration facts.

## Medicine-safety boundary

[The RxNorm wrapper](https://github.com/sujanshetty707/med-guardian/blob/4bad779a7e857ae0d98b43610da4dc74dd162acc/tools/rxnorm.py)
fabricates `SYN-...` identifiers for unresolved terms. Do not treat those as
verified medicine identities. Unknown names, strengths or forms must remain
unresolved and be reviewed. The refill path also uses a fixed strength, quantity
and US location, so it cannot be reused as an exact prescribed-item selection.

[Interaction screening](https://github.com/sujanshetty707/med-guardian/blob/4bad779a7e857ae0d98b43610da4dc74dd162acc/tools/openfda.py)
uses a small curated table and otherwise searches one first-returned label for
another drug's name. It is not a complete clinical interaction checker. A missing
match or failed source must not become a safety clearance. Preserve the exact
retrieved source/version and send concerns to a qualified pharmacist. The
existing English-only regex guardrails and US-oriented messages need deliberate
Hindi/Kannada and locale handling rather than translation assumptions.

[NLM describes RxNorm](https://www.nlm.nih.gov/research/umls/rxnorm/overview.html)
as drug terminology and identifiers; it is not stock, prescription authorization
or proof of purchase. [openFDA terms](https://open.fda.gov/terms/) do not guarantee
error-free data or uninterrupted service. Neither source supplies merchant
permission to dispense or refund medication.

## Concrete integration points

1. QuickFix owns the ACP cancellation/refund conversation. Add signed pharmacy
   request handlers in Med Guardian using the live v2 bridge described below.
   The [v1.0 contract](quickfix-contract.md) remains a separate sandbox schema.
   Its existing medication-assistance chat can remain separate; do not forward
   every customer as indistinguishable free-form text from one shared address.
2. Replace `PaymentManager` approval claims with verified results from QuickFix's
   configured gateway adapter. A UI approval is not a captured payment. New
   buyer checkout is outside this first cancellation/refund integration.
3. Med Guardian must supply authenticated customer-scoped orders and implement
   atomic pharmacy cancellation with order-version checks and duplicate protection.
   QuickFix owns the commerce case, approval, refund execution and recovery.
4. QuickFix queues separate customer and pharmacy events; the teammate mail
   agent owns sending, retries and delivery evidence. The local mail peer only
   captures messages. External sending requires authorized accounts/recipients.
5. Return typed pharmacy results to QuickFix. Keep medication stock unchanged
   until a future authenticated delivery/receipt event is implemented. Medicine
   returns, substitutions and dispensing remain pharmacy-controlled decisions.

Quotes, checkout, real buyer-payment relay and delivery events are future
contracts, not capabilities of the six-operation sandbox peer interface.

## Prepared live merchant bridge

QuickFix now supplies `orderfix.commerce.merchant.MerchantAPIAdapter` and
`make_merchant_protocol`. This is an integration contract for an authorized
merchant to implement. **It is not an assertion that Med Guardian, Costco, CVS,
Walgreens or any other pharmacy already exposes these endpoints.** No endpoint,
private seed or bearer token is bundled. No public catalog is treated as a
customer order, stock record or merchant permission.

The Med Guardian operator can include the protocol in its own privately seeded
agent after provisioning a real, authenticated merchant connector:

```python
import os
from orderfix.commerce.merchant import MerchantAPIAdapter, make_merchant_protocol

merchant = MerchantAPIAdapter(
    base_url=os.environ["MERCHANT_API_BASE_URL"],
    token=os.environ["MERCHANT_API_TOKEN"],
)
pharmacy_agent.include(make_merchant_protocol(
    merchant, coordinator_address=os.environ["QUICKFIX_COORDINATOR_ADDRESS"],
), publish_manifest=True)
```

Both operators must verify the privately controlled agent addresses and set
their allowlists. Copying a README address derived from a published seed is not
enough. Customer references must come from the coordinator's verified customer
link; a user-supplied order ID is not proof of ownership. The merchant must also
enforce this scope against its own account records.

Live requests use distinct `LivePeerRequest`/`LivePeerResponse` model digests,
`protocol_version="2.0"` and `mode="live"`. Signed sender checks, bounded UTC
envelope lifetime and matching correlation fields remain mandatory. Existing
sandbox v1 request/response digests are unchanged, and a sandbox peer cannot
receive a live request through its registered model handler.

### HTTPS API contract the merchant must implement

All requests authenticate with a private `Authorization: Bearer ...` token.
Redirects are disabled; no synthetic fallback or automatic mutation retry exists.
Responses use HTTP 200 and JSON. HTTP 401/403, 404, 409 and 422 report authorization,
missing orders, conflicts and rejected actions respectively. Other cancellation
failures, including timeouts, leave the cancellation outcome unknown and cannot
authorize a refund.

| Operation | HTTP endpoint | Successful result |
| --- | --- | --- |
| `orders.list` | `GET /orders?customer_ref=...` | `{"orders": [...]}`; at most 20; empty means no connected orders, never demo seeding |
| `order.get` | `GET /orders/{order_id}?customer_ref=...` | `{"order": ...}` |
| `order.eligibility` | `POST /orders/{order_id}/cancellation-eligibility` | `eligible`, `order`, `policy_text`, `refundable_amount_minor`, `currency`, `policy_version`, `eligibility_expires_at` |
| `order.cancel` | `POST /orders/{order_id}/cancel` | `order`, `cancellation_ref`, `refundable_amount_minor`, `currency`, `policy_version` |

Every order must validate as `PharmacyOrder`, contain `data_mode="connected"`,
have a nonempty `provider_order_id` from the payment provider, and match the
authenticated `customer_ref` and requested `order_id`. Required values include
the exact purchased item names/strengths/forms/quantities, integer minor-unit
amount, INR currency, payment ID, merchant/pharmacy references, recipient
addresses and order version. USD mock quotes must not be relabelled INR. The
adapter rejects incomplete records instead of guessing missing fields.

POST bodies carry the unchanged action payload plus `customer_ref`, `case_id`,
`expected_version` (the commerce case version) and `idempotency_key`. Every request
also has `Idempotency-Key` and `X-QuickFix-Case-Version` headers. The merchant must
durably bind the business idempotency key to the customer, operation and full
body. A duplicate replays the original result; a changed payload with the same
key returns HTTP 409, even across merchant restarts.

Eligibility requests contain `order_id`. Their response requires a strict
boolean, exact policy version/text, a UTC ISO timestamp within the next 15 minutes and an integer
refund amount no larger than the recorded order amount. Eligible orders must
still be accepted and have a positive refundable amount. The review uses those
merchant values without replacing them with the original purchase total.

Cancellation requests contain `order_id`, `expected_order_version`,
`approval_ref`, `refundable_amount_minor`, `currency="INR"`, `policy_version`
and the original UTC `eligibility_expires_at` timestamp.
The merchant must atomically recheck those values and its cancellation policy,
then perform the actual upstream cancellation before acknowledging it. A
successful response needs a genuine `cancellation_ref`, a cancelled order with
version exactly one greater than `expected_order_version`, and the identical
approved refund amount, currency and policy version. Only after verifying those
fields does the bridge add `cancelled=true` to its signed result. A database-only state
change without merchant cancellation evidence is not an implementation of this
contract. Refund execution remains QuickFix's separate gateway operation.

For recovery after a lost response, the merchant must look up the stable
idempotency key **before** applying current expiry or version checks. If the same
action already completed, replay its recorded result. Otherwise reject the first
cancellation attempt when `eligibility_expires_at` has passed. The bridge retains
that original timestamp, even when expired, so reconciliation never invents a
new approval or loses the ability to recover a previously completed operation.

Adapter tests inject `httpx.MockTransport`; no merchant, patient or gateway is
contacted. Passing those tests verifies the contract implementation, not a live
Med Guardian integration. The upstream `RefillOrder` table currently lacks these
required payment/order facts and must not be imported as authenticated purchase
evidence.
