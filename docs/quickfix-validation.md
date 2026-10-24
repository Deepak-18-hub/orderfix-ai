# QuickFix verification — 24 October 2026

The extension is running on the judging laptop under the existing registered
OrderFix identity. Agentverse showed Active and ASI:One Available after the
profile update. External teammate/payment/email integrations remain unverified.

## Automated results

Final command: `python -m pytest -q` with a fresh workspace temporary directory.
Result: **407 passed in 15.38 seconds** — 190 existing tests and 217 commerce
extension tests. The Windows restricted runner stalled; the successful run used
the normal local Python environment. `git diff --check` reported no whitespace
errors.

Coverage includes:

- Customer/conversation ownership, signed-peer allowlists, one-use account-link
  redemption under competing connections, and strict response correlation.
- Exact and expiring approvals, negative consent, duplicate cards/messages,
  immutable medicine/recipient facts, dispatch rejection and natural draft exit.
- Remaining refundable balance, merchant/currency checks, rejected live keys,
  provider idempotency, bounded read retries and ambiguous-write reconciliation.
- Restart recovery, lost cancellation responses, outages longer than request
  expiry, and rejection of saved approvals after integration settings change.
- Separate customer/pharmacy jobs, notification-only retries, queued/accepted/
  delivered evidence, restart-safe status polling and stale receipt rejection.
- Complete English/Hindi/Kannada flows, title/card selection without typed IDs,
  same-session background ACP cards, and preservation of legacy ACP routing.
- Laptop launcher identity preservation, simulation-only bindings, local peer
  isolation and restoration of configuration without editing `.env`.

Razorpay HTTP responses in tests are mocked. Connected-mail tests use fabricated
allowlisted responses and send no email.

## Actual local agent demonstration

Command:

```text
python -m orderfix.commerce.cli demo --output data/quickfix-final-demo-20261024
```

Completed successfully. Evidence is kept in the ignored local file
`data/quickfix-final-demo-20261024/evidence.json`, including a synthetic transcript.

| Measurement | Observed result |
| --- | --- |
| ACP requests / replies | 6 / 6, with 6 acknowledgements in each direction |
| Typed peer requests / replies | 5 / 5 |
| Peer operations | Order list, eligibility, cancellation, two notification requests |
| Simulated refund records | Exactly 1 |
| Captured emails | Exactly 2, one per recipient role |
| Final order / refund | Cancelled / processed in the local simulator |
| Valid signed HTTP loopback envelope | Accepted and handled once |
| Tampered HTTP loopback envelope | Rejected |
| Public peer registration | None |
| Real money / external email | None / none |

The commerce demonstration uses actual uAgents local dispatch and separate
agent identities. Local dispatch bypasses wire-envelope verification; the
signature check above is a **separate HTTP loopback probe**, not evidence of
deployed teammate traffic.

## External acceptance still outstanding

1. Med Guardian implements and answers the shared customer-scoped pharmacy
   contract. Its published address alone does not supply these operations.
2. A captured Razorpay test payment is prepared, private test credentials are
   configured, and the gateway exposes a matching processed/pending refund.
3. The teammate mail agent supports the contract, has an authorized sender and
   approved test recipients, and returns provider-backed delivery evidence.
4. Record the new 3–5 minute demo and complete the current hackpack's submission
   process. The earlier video covers only the original OrderFix workflow.

The judging launcher was started using the existing seed and mailbox. Its
loopback health endpoint returned `status: ok`. ASI displayed the synthetic order
carousel. The client adds `approved: true` on selection; the parser now accepts
that as selection intent only. Tests verify selection never executes a refund.
Temporary input diagnostics were removed. After intermittent ASI/Agentverse
timeouts recovered, the full English workflow completed in ASI:One: order card,
policy/refund review, approval button, cancelled order, processed simulated
refund, captured customer and pharmacy receipts, and status-button refresh.
Read-only database checks confirmed exactly **one simulated refund** and
**two captured receipts per order** across both live test conversations. No cloud host, new credentials or paid service was
introduced. No real money moved and no external email was sent.

Evidence:

- [New shared ASI:One conversation](https://asi1.ai/shared-chat/56e03728-b610-4698-b18e-8f250451fb5b).
- [Live final status screenshot](../assets/quickfix-live-status.png).
- Implementation commits `a783155` and `4ed3141` published to the existing public repository.
- [Public GitHub tests](https://github.com/Deepak-18-hub/orderfix-ai/actions/runs/40077127283).

The live deployment verification used English. Hindi/Kannada QuickFix coverage
is automated; do not describe that as live multilingual acceptance evidence.
Laptop uptime, connectivity and the background judging process are required.
