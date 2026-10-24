# Med Guardian integration review

Read-only source review of [sujanshetty707/med-guardian at
0fc48c954478e53b438e317f04682e180aad57cc](https://github.com/sujanshetty707/med-guardian/tree/0fc48c954478e53b438e317f04682e180aad57cc).
This records observed code at that commit; it does not verify a deployed service
or claim the upstream project has applied these changes. No upstream repository
was modified and its code was not executed during review.

The public GitHub API was checked again on 24 October 2026: `main` still pointed
to this commit (committed at 03:41:35 UTC). Its README publishes the following
addresses; this review does **not** verify their current operator, registration,
availability or compatibility with the QuickFix contract:

- Guardian Orchestrator: `agent1q09642fqjyuhjrcpupdwcskl7n3890zw6y6py3n8ugcfjvw8cayr6zcd0lx`
- Pharmacy specialist: `agent1q0xte8vak3zx99s5d9sm6qp0s9jkrf6a4kul7598uuwt03efax4fgn43sgn`

Source: [pinned registration section](https://github.com/sujanshetty707/med-guardian/blob/0fc48c954478e53b438e317f04682e180aad57cc/README.md#6-agentverse-registration).
`MEDGUARDIAN_AGENT_ADDRESS` must identify the actual peer implementing the
commerce contract; do not insert a published ACP-only address and assume that it
can answer typed pharmacy requests.

## Reusable implementation

The project supplies a Python/uAgents ACP orchestrator, typed specialist-message
models, SQLAlchemy persistence, medication parsing, RxNorm/openFDA wrappers and
Telegram notification code. Its [Bureau launcher](https://github.com/sujanshetty707/med-guardian/blob/0fc48c954478e53b438e317f04682e180aad57cc/scripts/run_agents.py)
creates five agents. The ordinary [orchestrator conversation path](https://github.com/sujanshetty707/med-guardian/blob/0fc48c954478e53b438e317f04682e180aad57cc/agents/orchestrator/agent.py)
calls tools and the shared database directly rather than delegating its workflow
to those specialist agents. Therefore a new integration must demonstrate actual
messages and responses, not infer collaboration from the five instances.

## Transaction and identity blockers

| Observation at the pinned commit | Required integration boundary |
| --- | --- |
| [PaymentManager](https://github.com/sujanshetty707/med-guardian/blob/0fc48c954478e53b438e317f04682e180aad57cc/tools/payments.py) creates a dictionary/random `pay_` reference; verification always returns true. | Treat it as a stub. Real payment status must come from an authenticated adapter and verified settlement evidence. |
| [Approval parsing](https://github.com/sujanshetty707/med-guardian/blob/0fc48c954478e53b438e317f04682e180aad57cc/core/llm.py) tests substrings such as approve/pay before cancellation; a negated approval can match. | Require exact, single-use, expiring approval tied to customer, case, amount and version. Never authorize through LLM classification. |
| [Refill approval](https://github.com/sujanshetty707/med-guardian/blob/0fc48c954478e53b438e317f04682e180aad57cc/agents/orchestrator/agent.py) immediately adds 30 units and reports an authorized order without calling a payment gateway or persisting the specialist order. | Keep payment, pharmacy acceptance, shipment and receipt separate. Increase home stock only after receipt is verified. |
| [SessionManager](https://github.com/sujanshetty707/med-guardian/blob/0fc48c954478e53b438e317f04682e180aad57cc/core/acp.py) keys state only by sender; the orchestrator derives patient IDs from a truncated sender prefix. | A bridge sending all users from one agent address would mix state. Use full authenticated user scope plus explicit conversation/case correlation. |
| [Specialist models](https://github.com/sujanshetty707/med-guardian/blob/0fc48c954478e53b438e317f04682e180aad57cc/core/models.py) carry patient IDs, but handlers do not establish caller ownership. | Allowlist the signed actual sender and bind opaque customer scope to verified consent at the coordinator. |
| [Card metadata](https://github.com/sujanshetty707/med-guardian/blob/0fc48c954478e53b438e317f04682e180aad57cc/core/cards.py) is custom; incoming ACP extraction reads text only. | Verify real ASI callback/text delivery and retain exact text confirmation fallback; do not assume metadata makes a functioning native card. |

The configured default seeds and deployment seeds are public in the repository.
Production agent identities need private stable seeds and fresh allowlist entries;
an address derived from a published seed is not an exclusive identity.

## Data and operational limits

- [Mock pharmacy quotes](https://github.com/sujanshetty707/med-guardian/blob/0fc48c954478e53b438e317f04682e180aad57cc/tools/pricing/mock_partner.py)
  use hardcoded prices and real chain names with fabricated pickup promises.
  `estimated=False` does not make them merchant quotes. Use fictional pharmacy
  names and an explicit simulation marker until a permitted merchant feed exists.
- [NADAC pricing](https://github.com/sujanshetty707/med-guardian/blob/0fc48c954478e53b438e317f04682e180aad57cc/tools/pricing/nadac.py)
  is a hardcoded unit-cost table, not a live CMS lookup or dated benchmark import.
- [The pharmacy agent](https://github.com/sujanshetty707/med-guardian/blob/0fc48c954478e53b438e317f04682e180aad57cc/agents/pharmacy/agent.py)
  can save a local order after unconditional payment verification, but does not
  contact a merchant, check stock, validate a prescription or book delivery.
- [Notifications](https://github.com/sujanshetty707/med-guardian/blob/0fc48c954478e53b438e317f04682e180aad57cc/tools/notify/dispatcher.py)
  implement a Telegram branch; unsupported channels can return true without
  delivery. No order-email or refund adapter exists in the reviewed tree.
- The normal schedule-confirmation path creates a demo dose event rather than a
  persisted recurring Schedule. The schedule agent's source does not register a
  recurring reminder worker. Reminder, caregiver-consent and weekly-summary
  claims require independent verification before they become integration facts.

## Medicine-safety boundary

[The RxNorm wrapper](https://github.com/sujanshetty707/med-guardian/blob/0fc48c954478e53b438e317f04682e180aad57cc/tools/rxnorm.py)
fabricates `SYN-...` identifiers for unresolved terms. Do not treat those as
verified medicine identities. Unknown names, strengths or forms must remain
unresolved and be reviewed. The refill path also uses a fixed strength, quantity
and US location, so it cannot be reused as an exact prescribed-item selection.

[Interaction screening](https://github.com/sujanshetty707/med-guardian/blob/0fc48c954478e53b438e317f04682e180aad57cc/tools/openfda.py)
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
   request handlers in Med Guardian using the [v1.0 contract](quickfix-contract.md).
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
