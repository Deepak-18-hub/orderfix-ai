# Six-hour delivery plan

This plan assumes a four-person team, with one person currently building. The deadline is six hours from the team's chosen start time. Keep the ₹0 default: local execution, synthetic merchant data, deterministic English/Hindi/Kannada support, and an optional ASI1 key only if the team already has suitable access. Do not buy hosting or API credits without a new budget decision.

## Work ownership when teammates join

| Owner | Responsibility | Definition of done |
|---|---|---|
| Builder | Engine, policy, persistence, repeat-request handling, and tests | Four workflows pass locally, and no return is created before a matching explicit confirmation. |
| Platform teammate | Agentverse profile, ACP/ASI:One discovery, endpoint/mailbox setup, and uptime | A new ASI:One conversation can reach the running agent and complete a return request. Record its real profile URL. |
| QA teammate | English/Hindi/Kannada walkthroughs, expired/excluded orders, duplicate confirmation, and recovery checks | Save reproducible results; have a fluent Kannada/Hindi speaker check naturalness where possible. |
| Demo teammate | README review, Devpost text, public repository visibility, shared-chat link, and video | Submission links work and every demonstration claim matches evidence. |

Until teammates join, do the builder and platform work first. Prepare handoffs through the repository and this document; the plan does not require messaging anyone from the agent.

## Critical path

| Elapsed time | Deliverable | Decision gate |
|---|---|---|
| 0:00–1:15 | Working local store, policy evaluation, review and confirmation, persisted request | Finish one English happy path before optional integrations. |
| 1:15–2:15 | Tests and all three template languages | Test expired, unfulfilled, excluded, unavailable-quantity, and repeated-request cases. |
| 2:15–3:15 | Agentverse profile and live ACP connection | ASI:One must be able to reach the agent. Fix this before adding new features. |
| 3:15–4:15 | Full ASI:One workflow and interactive review if supported | Confirm that tool actions, statuses, and token handling survive the ASI:One conversation. |
| 4:15–5:00 | Public repository and clean setup instructions | A teammate can install and run the project without personal files or leaked secrets. |
| 5:00–6:00 | Shared conversation, short recording, Devpost submission, final uptime check | Open every link and run one final new conversation. Freeze optional scope. |

The code may be completed sooner than this allocation; move saved time to live integration, recording, and verification. Local test success alone does not satisfy the ASI:One requirement.

## Scope to protect

Deliver order lookup, policy eligibility, explicitly approved return submission, and status lookup. The backend action in the default demo is an actual local database write followed by retrieval. It is not a purchase, a Shopify mutation, a refund, a pickup booking, or a retailer-wide integration.

Keep arbitrary real-customer access, email OTP, actual refunds, payment protocols, multiple retailers, attachment evidence, and continuous monitoring outside the six-hour MVP. A Shopify development-store adapter can be exercised if account setup fits the remaining time; never let that delay the mandatory ASI:One integration.

## Handoff packet

Each teammate should receive:

- The repository URL and current branch/commit.
- The current feature and test status, including any failures.
- The chosen demo mode and exact commands from `demo-script.md`.
- The Agentverse profile and ASI:One shared-chat URL when available.
- The person responsible for keeping the local process or authorized host running.

Share secrets through the team's normal private secret-management route, not GitHub, chat transcripts, recordings, or documentation. `.env` and databases remain local. Do not rotate the agent seed casually: it determines the agent identity used by registration.

## Remaining launch decisions

Before judging, confirm that the deployment method remains available for the judging period, supports the required inbound or mailbox connection, and preserves the database. A sleeping or ephemeral host can make an otherwise working demonstration unavailable. If no zero-cost persistent host is available, use the laptop deployment with its actual availability stated honestly.
