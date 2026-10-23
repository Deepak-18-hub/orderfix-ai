# OrderFix — submission packet

Prepared on **23 October 2026**. The repository, registered agent, direct ASI:One entry point and complete shared conversation are verified. The three-minute actual-screenshot walkthrough has been exported. **A separate externally hosted video-embed URL is not configured, and the Devpost form has not been submitted.**

## Name and one-line pitch

**OrderFix: from shopping frustration to a verified return request, inside ASI:One.**

## Problem

Returning an online purchase often requires finding the order, interpreting store rules, choosing the right item, and repeatedly asking support what happened. Customers can mistake a conversation for a completed request, while stores handle the same lookup and policy questions repeatedly.

## Target users

Customers who want help with an order or return in English, Hindi, or Kannada, and small merchants who want a guided after-sales workflow with explicit customer approval.

## What it does

OrderFix supports four connected workflows entirely through conversation: order lookup, return-eligibility evaluation, return-request submission after a review and explicit confirmation, and retrieval of the recorded request status. The workflow checks the selected item and quantity against policy, presents the details for review, creates a persistent request, and verifies the returned record. Repeated confirmation reconciles the existing operation instead of blindly creating another request.

The default demonstration uses synthetic orders and a persistent SQLite merchant backend. Requests are real local writes, clearly identified as demo data. No purchases, refunds, parcel pickup, or shipment of goods occur. The optional Shopify adapter targets an authorized development store and controlled test customers; its mocked tests pass, but no live Shopify validation has been performed.

## Why it goes beyond chat

The conversation gathers missing details, selects an order and item, applies policy constraints, creates a bounded review, waits for explicit confirmation, calls a merchant provider, persists the result, and reads status back. Eligibility and transaction state come from deterministic code and stored records. A model cannot grant access, approve an action, invent a return reference, or report an unverified successful mutation.

## Languages and architecture

- Python 3.12, `uagents==0.26.1`, `uagents-core==0.4.12` and ACP `0.3.0`.
- Persistent sessions, local merchant data, operation journal, and message replay protection.
- English, Hindi, and Kannada intent rules and fixed transaction-response templates.
- Optional ASI1 intent classification for unfamiliar phrasing; the documented workflows run without an API key.
- English and Hindi interactive review cards with live click confirmations verified, plus an exact text-confirmation path.
- Optional Shopify test-store provider with merchant-controlled customer access.

The MVP uses one coordinating agent. Its tool orchestration and verified state changes are the core demonstration. Multi-agent collaboration, Payment Protocol integration and universal retailer support are not implemented.

## Credible continuation

A merchant subscription could fund deployment and additional store integrations. Before serving real shoppers, the next development phase needs proper customer identity verification/account linking, merchant-specific return policies, operational monitoring, privacy and retention controls, and tested production merchant permissions. The MVP does not implement billing or payment handling.

## Verified submission links

| Required evidence | Verified value or current state |
| --- | --- |
| ASI:One shared chat showing all four workflows | [Complete OrderFix conversation](https://asi1.ai/shared-chat/aae314b3-dd9a-423d-bce4-ebe4486b0d5d) |
| Agentverse profile | [OrderFix profile](https://agentverse.ai/agents/details/agent1qdhv4kkryldes33rtzvpr6uznhdup5tdz9apfthxw7r7r8jv930ccrqspns/profile) |
| Public GitHub repository | [Deepak-18-hub/orderfix-ai](https://github.com/Deepak-18-hub/orderfix-ai) |
| Short demonstration video | [Download MP4](../assets/orderfix-demo.mp4), [English captions](../assets/orderfix-demo.srt); 3:00, 1080p |
| External video-embed URL | Not configured; the actual MP4 is ready for upload |
| Setup and test instructions | [Public README](https://github.com/Deepak-18-hub/orderfix-ai#readme) |
| Direct ASI:One entry | [@orderfix-ai](https://asi1.ai/ai/agent1qdhv4kkryldes33rtzvpr6uznhdup5tdz9apfthxw7r7r8jv930ccrqspns) |
| Source CI | [Successful tested-source workflow for 3ecd73d](https://github.com/Deepak-18-hub/orderfix-ai/actions/runs/39999085198) |
| Runtime during judging | User-selected laptop mailbox runtime; keep laptop, Python process and network online |

The video is labeled as a walkthrough assembled from seven actual captured screenshots. It is not a continuous live screen recording. The MP4 and captions are included in repository assets for publication with this packet; add an appropriate external host URL if required by Devpost's video field.

## What the shared conversation proves

The [shared chat](https://asi1.ai/shared-chat/aae314b3-dd9a-423d-bce4-ebe4486b0d5d) was opened and checked in the browser. It shows order lookup, an English review card in its submitted state, a saved request `DEMO-R-4A7E9E97CA19`, and a subsequent status response with that same ID. It also shows Hindi status retrieval and a Kannada rejection of expired order `#1002`.

In a separate live Hindi card test, confirmation saved `DEMO-R-ED45E74ED18E`; repeated confirmation returned the existing reference instead of creating another request. Complete local eligible-return flows passed in English, Hindi and Kannada. Live Kannada evidence currently covers policy rejection, rather than a complete eligible-return submission.

After restarting the local agent, a live explicit `@orderfix-ai confirm TOKEN` retry recovered the existing `DEMO-R-1D7276382C16` and preserved the conversation's Kannada response language. This separate check verified durable state and duplicate protection across a process restart.

The Agentverse profile has a custom avatar and was observed as Active and ASI:One Available. The direct profile works, and exact `@orderfix-ai` lookup in ASI:One's recipient picker returns OrderFix. Broad keyword recommendations or global-search ranking have not been tested.

The full local automated suite passed **190 tests**, and the published source CI workflow succeeded. See the [validation record](validation.md) for current evidence and exact coverage. Tests include persistence, access isolation, duplicates, concurrent quantities, expired reviews, uncertain outcomes and ACP behavior. The store mode used in the shared demo is synthetic `demo`, not Shopify.

## Judging availability

The user selected **keeping the laptop online for judging**. The local agent retrieves requests through its Agentverse mailbox and retains the database on disk. Cloud hosting is deferred and no public tunnel is required. Keep the laptop powered, awake and connected, and follow the [start/restart checklist](windows-setup.md#laptop-judging-start-and-restart-checklist).

## Copy-ready short Devpost description

OrderFix turns shopping complaints into verified return requests inside ASI:One. It retrieves the order, checks eligibility, presents the exact item and quantity for approval, submits the request, and reads its saved status back. English, Hindi and Kannada responses make the workflow accessible. Interactive review cards guide confirmation, while persistent state and operation recovery prevent duplicate submissions. The shared ASI:One chat demonstrates the complete workflow with a matching saved return reference and multilingual responses. Our demo uses clearly labeled synthetic orders and real SQLite persistence; no money moves. The project also includes an optional Shopify development-store adapter covered by mocked tests.

## Remaining submission steps

1. Upload the completed MP4 to a video host supported by Devpost and place its external URL in the video field. The download file is ready; no separate video-host URL has been created.
2. Copy the verified links and description into Devpost, review the form and submit it. No Devpost submission has been performed by this work.
3. Keep the selected laptop mailbox runtime available during judging.
