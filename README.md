# OrderFix — from shopping problems to verified return requests

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

OrderFix helps shoppers look up an order, check whether an item can be returned, submit a return request after reviewing it, and retrieve its recorded status. The primary interaction is designed for an **ASI:One conversation**, using the **Agent Chat Protocol (ACP)**. English, Hindi and Kannada are supported.

**The delivered default is a synthetic merchant demo with real local persistence.** Confirming a review creates a durable SQLite return record, reserves the returned quantity, and reads the saved record back before reporting success. No purchase, shipment or refund takes place.

## Deployment and verification status

<!-- DEPLOYMENT_STATUS: replace pending entries only after verifying each external result. -->
| Item | Status |
| --- | --- |
| Local implementation | Complete; 190 automated tests passed on 23 October 2026 |
| Scripted workflow | Verified locally in English, Hindi and Kannada |
| GitHub Actions | [Tested-source workflow passed for 3ecd73d](https://github.com/Deepak-18-hub/orderfix-ai/actions/runs/39999085198); local full-suite count is 190 |
| Public GitHub repository | [Deepak-18-hub/orderfix-ai](https://github.com/Deepak-18-hub/orderfix-ai) |
| Agentverse profile | [OrderFix profile](https://agentverse.ai/agents/details/agent1qdhv4kkryldes33rtzvpr6uznhdup5tdz9apfthxw7r7r8jv930ccrqspns/profile) registered with a custom avatar; observed Active and ASI:One Available |
| ASI:One direct agent | [@orderfix-ai](https://asi1.ai/ai/agent1qdhv4kkryldes33rtzvpr6uznhdup5tdz9apfthxw7r7r8jv930ccrqspns); English lookup, eligibility, confirmed submission and status verified live |
| ASI:One shared chat | [Verified complete workflow](https://asi1.ai/shared-chat/aae314b3-dd9a-423d-bce4-ebe4486b0d5d): lookup, submitted review card, saved request, matching status, Hindi status and Kannada policy rejection |
| ASI:One interactive cards | English and Hindi review cards rendered; click confirmations produced verified requests |
| Demo walkthrough | [Download the 3-minute MP4](assets/orderfix-demo.mp4) and [English captions](assets/orderfix-demo.srt); actual screenshots, 1080p |
| Shopify development-store integration | Adapter and mocked tests included; not validated against a live store |
| Judging runtime | Laptop mailbox runtime selected by the user; keep the computer, agent process and network online. Cloud hosting deferred |
<!-- /DEPLOYMENT_STATUS -->

See the [validation record](docs/validation.md) and [submission packet](docs/submission.md) for verified evidence and copy-ready project details. The complete shared chat and a three-minute walkthrough using actual screenshots are available. A separate externally hosted video-embed URL has not been created, and the Devpost form has not been submitted. Judging will use the selected laptop mailbox runtime.

## What the agent does

| Workflow | Action and outcome |
| --- | --- |
| Order lookup | Reads the customer's order, fulfillment status, items and available quantities |
| Return eligibility | Applies an explicit sample policy and explains the reason for rejection |
| Return submission | Presents an immutable review, requires its confirmation token, writes the request, then verifies the saved result |
| Return status | Reads the latest persisted request and distinguishes a request from merchant approval or a refund |

The default sample policy permits returns within 14 days after fulfillment, excluding flagged items and quantities already reserved by another return. Missing, timezone-less or future fulfillment dates require manual review. Sample timestamps persist across restarts; restarting the agent does not renew a return window.

The agent does not claim to approve refunds, pay money, buy products, book pickups or create shipping labels. Photo evidence and email/OTP verification are outside this version. The optional Shopify mode is a merchant-controlled test pilot, not authentication for arbitrary real shoppers.

## Run locally — no API key required

Use Python 3.12. From the downloaded or cloned repository folder in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m orderfix.setup_local
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m orderfix.cli --scripted --language en
.\.venv\Scripts\python.exe -m orderfix.cli --scripted --language hi
.\.venv\Scripts\python.exe -m orderfix.cli --scripted --language kn
```

`setup_local` creates `.env` with a private, stable agent seed and preserves an existing `.env`. Leave `STORE_MODE=demo` and `ASI1_API_KEY` empty for the no-cost local demo. Secrets and databases are excluded from Git.

Each scripted invocation starts a new synthetic customer conversation, so all three language runs can use the same sample order numbers independently. The script automatically confirms its synthetic demo review and verifies duplicate-message replay; it refuses scripted execution in Shopify mode.

To try the conversation yourself:

```powershell
.\.venv\Scripts\python.exe -m orderfix.cli --language en --session my-demo
```

Use the same `--session` to resume the same local customer. Type `quit` to exit. See [Windows setup and troubleshooting](docs/windows-setup.md) for the delivered Codex runtime alternative and Unicode output settings.

## Example conversation

```text
order #1001
return #1001 item 1 quantity 1 damaged
confirm TOKEN
return status #1001
```

Replace `TOKEN` with the 12-character token shown in the review. Review cards provide the same confirmation and cancellation choices when supported by the client. The text commands remain available. A review expires after 15 minutes; changing its details invalidates the old token.

Switch language with `English`, `हिंदी`, or `ಕನ್ನಡ`. The equivalent sample complaints are:

```text
ऑर्डर #1001 का आइटम 1 खराब है, वापस करना है
ಆರ್ಡರ್ #1001 ವಸ್ತು 1 ಹಾಳಾಗಿದೆ, ವಾಪಸ್ ಮಾಡಬೇಕು
```

Replies, policy explanations, reasons and card controls use authored language templates. Product names remain as supplied by the store. Optional ASI1 classification helps with unfamiliar phrasing when a key is configured; it cannot grant access, choose order identifiers or authorize a return.

### Sample orders

Each conversation receives its own copy on first lookup.

| Order | Scenario |
| --- | --- |
| `#1001` | Two wireless headphones and one USB-C cable, fulfilled three days before the customer's first lookup |
| `#1002` | Fulfilled 30 days earlier; outside the return window |
| `#1003` | Not fulfilled; automatic return submission unavailable |
| `#1004` | Excluded digital music voucher |
| `#1005` | Existing `REQUESTED` return; available quantity already reserved |

After a successful submission, repeat the exact confirmation command. The agent returns the existing verified result instead of submitting it twice. If a submission result is uncertain, the journal prevents another action until the first can be reconciled. Use `status` to check it.

## Run the ACP agent

First create an Agentverse account API key and save it privately as `AGENTVERSE_API_KEY` in `.env`. This key associates the agent's mailbox with your account; it is separate from the optional ASI1 model API key. Keep the existing `AGENT_SEED` unchanged. Register the mailbox once, then start the agent:

```powershell
.\.venv\Scripts\python.exe -m orderfix.register --dry-run
.\.venv\Scripts\python.exe -m orderfix.register --register
.\.venv\Scripts\python.exe -m orderfix.agent
```

The preview prints the public address and registration settings without exposing the seed or account key. Only `--register` writes the mailbox registration. Rerunning it uses the same identity and updates the existing record in that account.

The agent uses `uagents==0.26.1`, `uagents-core==0.4.12`, and the bundled **ACP 0.3.0** specification. It acknowledges incoming chat messages, preserves the authenticated sender/session boundary, and publishes its protocol manifest. Mailbox mode is enabled.

The process also serves a small `GET /health` endpoint on `AGENT_PORT` (default `8001`) to keep this SDK's runtime active. The legacy Inspector is disabled. The health endpoint does not need to be exposed publicly and is not a substitute for a successful mailbox conversation.

The current Agentverse documentation confirms that mailbox agents retrieve queued messages without a publicly exposed endpoint, so this setup does not require a tunnel. Keep the process running and check the agent's Testing tab in Agentverse before testing ASI:One discovery. [Official mailbox guidance](https://docs.agentverse.ai/documentation/launch-agents/agentverse-sdk/overview)

The registration helper replaces the legacy Inspector setup route. Preserve the seed, confirm the resulting agent belongs to the intended account, and verify the profile and mailbox connection. The exact public status belongs in the table above; a helper invocation or process startup alone is not proof of a complete ASI:One workflow.

The profile description is in [AGENT_PROFILE.md](AGENT_PROFILE.md). The [verified shared conversation](https://asi1.ai/shared-chat/aae314b3-dd9a-423d-bce4-ebe4486b0d5d) completes lookup → review → explicit confirmation → status **inside ASI:One**, without a custom frontend. The direct profile works, and the recipient picker finds the exact `@orderfix-ai` handle. Broad keyword recommendations or global-search ranking have not been tested. The CLI is only a development harness.

## Configuration

| Variable | Purpose |
| --- | --- |
| `AGENT_SEED` | Private agent identity; generated once by `setup_local` |
| `AGENTVERSE_API_KEY` | Private Agentverse account key used by the one-time mailbox registration helper |
| `AGENT_PORT` | Local agent port, default `8001` |
| `ORDERFIX_DB` | Agent state and merchant demo database, default `data/orderfix.sqlite3` |
| `STORE_MODE` | `demo` by default; optional controlled `shopify` test mode |
| `ENABLE_CARDS` | Include review metadata, default `true` |
| `ASI1_API_KEY` | Optional intent classification; empty means no ASI1 API calls |
| `ASI1_MODEL` | Optional classifier model, default `asi1` |

The CLI defaults to `data/local-demo.sqlite3`; use `--db` to choose another file. Its `--mode` flag explicitly selects `demo` or `shopify`. Agent mode uses `.env` settings.

### Optional Shopify test adapter

The connector uses Shopify Admin GraphQL API version `2026-10` and `returnRequest`. Its tests use mocked HTTP responses. **No live Shopify validation or merchant permission is implied.** The current policy remains the clearly labeled sample policy; production merchant rules must be implemented before use with real orders.

For a merchant-authorized development store, configure `SHOPIFY_TEST_STORE=true`, `STORE_MODE=shopify`, and `SHOPIFY_SHOP_DOMAIN=your-store.myshopify.com`. Supply either an authorized `SHOPIFY_ACCESS_TOKEN` or the app's `SHOPIFY_CLIENT_ID` and `SHOPIFY_CLIENT_SECRET` through private environment configuration. Confirm the app has the needed order and return access in the merchant's account before testing.

The operator issues a short-lived test-customer invitation with:

```powershell
.\.venv\Scripts\python.exe -m orderfix.invite "gid://shopify/Customer/TEST_CUSTOMER_ID"
```

The invite is bound to one chat when redeemed and expires after two hours. It is a controlled testing credential, not email verification. Use the same database for the invitation and agent; do not publish a shared chat containing an active invitation. Live Shopify usage is a separate validation step and is not needed for the default local demonstration.

## Reliability and structure

The tests cover policy boundaries, customer/session isolation, persistent state, duplicate transport delivery, concurrent quantities, review expiry, invalidated approvals, uncertain-result recovery, language flows, ACP messages and mocked Shopify failures. GitHub Actions runs the same suite; the first published run passed on Linux.

The current 190-test local suite also covers ASI:One's leading recipient address/public handle and optional `approved` boolean in review-card selections. Only this agent's exact leading routing mention is removed. Card approval must match the requested action as a real boolean; conflicting decisions, strings and numbers cannot authorize a return.

The agent is a single coordinator with explicit tools, durable workflow state and a deterministic policy engine. It does not claim multi-agent orchestration. See [architecture and action safety](docs/architecture.md) for the module graph and journal behavior.

For judging, the user has selected keeping the laptop online. Keep the process and database available; mailbox delivery provides connectivity but does not host Python. Follow the [laptop start/restart checklist](docs/windows-setup.md#laptop-judging-start-and-restart-checklist). Cloud hosting is deferred. A future move requires persistent storage, the same private seed and environment settings, and a new end-to-end ASI:One check. The included Dockerfile packages the agent; no cloud account or recurring subscription is required for the chosen runtime.

Potential monetization is a merchant subscription for after-sales request handling. Payments and the Payment Protocol are not implemented in this version.

See [judging runtime and Docker deployment](docs/deployment.md) for the persistent-volume configuration and the remaining hosting validation steps.

For a future cloud move, the current SQLite build cannot be assumed to run unchanged in Agentverse's hosted editor. A verified Railway trial with a persistent volume is a possible separate host, subject to account eligibility and credit limits; no Railway service has been created. The [validation record's hosting assessment](docs/validation.md#hosting-assessment) records this optional research and official evidence.
