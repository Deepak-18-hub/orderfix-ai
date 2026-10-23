# Validation record

Status recorded on **23 October 2026**. The running integration uses synthetic merchant data and a persistent local SQLite database. Registered mailbox connectivity does not imply that Python is hosted by Agentverse.

## Completed checks and remaining evidence

| Area | Verified result | Scope or remaining work |
| --- | --- | --- |
| Local automated tests | 190 passed in 9.77 seconds | Full suite after the bounded public-handle fix; includes strict card-decision checks |
| Targeted ACP tests | 32 passed after the public-handle routing fix | `tests/test_acp.py`: routing and ACP handler coverage |
| Latest published source CI | [Workflow passed for 3ecd73d](https://github.com/Deepak-18-hub/orderfix-ai/actions/runs/39999085198) | Final tested source is published; local full-suite count is 190 |
| Initial published CI | [159 tests passed on Linux](https://github.com/Deepak-18-hub/orderfix-ai/actions/runs/39992502631) | Retained as historical evidence |
| Local scripted demos | English, Hindi and Kannada completed | Synthetic lookup, policy evaluation, confirmation, committed readback, duplicate replay and status |
| Public source | [orderfix-ai repository](https://github.com/Deepak-18-hub/orderfix-ai) is published | Tested source [3ecd73d](https://github.com/Deepak-18-hub/orderfix-ai/commit/3ecd73d6540b13536bfccdd86814986011479427) is published; later documentation/media commits do not change this source validation |
| Agentverse | [OrderFix profile](https://agentverse.ai/agents/details/agent1qdhv4kkryldes33rtzvpr6uznhdup5tdz9apfthxw7r7r8jv930ccrqspns/profile) registered with a custom avatar | Observed Active, ASI:One Available and 17 interactions; runtime remains local |
| Live ASI:One English | Fresh lookup → review card submitted → saved `DEMO-R-4A7E9E97CA19` → same ID retrieved in status | Complete primary workflow verified in the shared conversation; manual text confirmation also verified separately |
| Live Hindi | Shared conversation returns Hindi status for the same request; a separate Hindi card produced `DEMO-R-ED45E74ED18E` | Repeated confirmation of the Hindi card returned the existing ID without a duplicate |
| Live restart and retry | After process restart, explicit `@orderfix-ai confirm TOKEN` returned the existing `DEMO-R-1D7276382C16` | Persistence and duplicate protection verified live; reply retained the conversation's Kannada language |
| Live Kannada | Shared conversation rejects expired order `#1002` correctly in Kannada | Local complete eligible-return flow passed; no live Kannada eligible-return completion is claimed |
| ASI:One shared chat | [Verified complete shared conversation](https://asi1.ai/shared-chat/aae314b3-dd9a-423d-bce4-ebe4486b0d5d) | Opened and checked in the browser; contains lookup, card, submission, matching status and multilingual responses |
| ASI:One entry point | Direct profile works; exact `@orderfix-ai` lookup in the recipient picker returns OrderFix | Broad keyword recommendations or global-search ranking have not been validated |
| Shopify | Mocked adapter tests passed | No live development-store validation |
| Judging runtime | User selected keeping the laptop online | Registered mailbox runtime uses the local process/database; cloud hosting deferred |
| Demo video | [MP4](../assets/orderfix-demo.mp4): exactly 3:00, 1080p; [English captions](../assets/orderfix-demo.srt) | Seven actual screenshots assembled into a labeled walkthrough; no separate external video-embed URL |
| Devpost | Submission packet prepared | Form submission has not been performed |

Direct agent entry: [@orderfix-ai on ASI:One](https://asi1.ai/ai/agent1qdhv4kkryldes33rtzvpr6uznhdup5tdz9apfthxw7r7r8jv930ccrqspns).

The video is a labeled walkthrough assembled from actual captured screenshots, not a continuous live screen recording. The shared chat is the inspectable conversation evidence. The [submission packet](submission.md) contains the verified URLs and project description. The MP4 and captions are included in repository assets for publication with this documentation; a compatible external video host may still be needed for Devpost's video-embed field.

## Reproduce local checks

From the project folder with the prepared virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m orderfix.cli --scripted --language en
.\.venv\Scripts\python.exe -m orderfix.cli --scripted --language hi
.\.venv\Scripts\python.exe -m orderfix.cli --scripted --language kn
```

Each scripted run uses a new demo conversation unless a session is supplied. It writes synthetic records locally, never issues refunds, and requires no ASI1 key. The terminal prints `VERIFIED` only after its result assertions pass. [Windows setup](windows-setup.md) includes the delivered machine's alternate interpreter path.

The automated suite covers the stateful paths that make return submission reliable: customer/session ownership, quantity reservations, duplicate operation payloads, simultaneous confirmations, stale and expired reviews, restart persistence, known failures, ambiguous actions and reconciliation. It also checks ACP acknowledgement/response contracts and HTTP adapter failure handling.

## Repeat the live workflow

1. Keep `python -m orderfix.agent` running with the registered seed and persistent database.
2. Open a fresh conversation through the direct agent link. Use one conversation for the entire workflow.
3. Send `order #1001`, then `return #1001 item 1 quantity 1 damaged`.
4. Review the exact item, quantity, reason and sample policy. Click the review card or send its exact `confirm TOKEN` command.
5. Check that the response contains a verified demo return reference with a requested/pending status.
6. Send `return status #1001` and verify the same reference is read back.
7. Reuse the same confirmation token; verify that no duplicate return is created.
8. Capture a shared chat URL only after the complete workflow is visible. Use a fresh conversation for another independent language demonstration.

`REQUESTED` is a submission awaiting merchant handling. It is not refund approval, a pickup booking or a payment. A sample record created in a different conversation belongs to that other synthetic customer namespace.

## Hosting assessment

**The user selected the laptop mailbox runtime for judging; cloud hosting is deferred.** No cloud host has been created or validated, and no hosting account is currently requested. The local `/health` server keeps the installed SDK's process active; no public health port or tunnel is needed. The [laptop checklist](windows-setup.md#laptop-judging-start-and-restart-checklist) covers start/restart checks. The alternatives below are future research; a move would require durable database storage and a full post-migration conversation test.

**Agentverse hosted editor:** its documented model resets global variables between invocations and uses Agent Storage for persistence. We infer that the current long-running coordinator and file-backed SQLite journal require adaptation before using that runtime; simply uploading this project is not a verified deployment path. Its supported imports must also be checked. [Hosted Agents](https://docs.agentverse.ai/documentation/create-agents/hosted-agents), [Allowed Imports](https://docs.agentverse.ai/documentation/advanced-usages/allowed-imports)

**Railway candidate:** an account with a verified Full Trial offers unrestricted outbound networking; Limited Trial networking may not support the mailbox reliably. The published trial offers a one-time $5 credit lasting at most 30 days, with actual duration depending on usage. These are limited credits, not a promise of permanently free hosting. [Official trial terms](https://docs.railway.com/pricing/free-trial)

Railway supports persistent volumes, with a documented 0.5 GB default for Free/Trial plans. A possible deployment would mount a volume for `ORDERFIX_DB`, run one worker with the preserved seed, confirm write permissions, and verify restart recovery. The included image uses a non-root user, so volume ownership must be checked. The platform also documents restrictions on replicas and volume lifecycle. This option has not been provisioned or tested. [Volume reference](https://docs.railway.com/volumes/reference)

With the current ₹0 spending limit, selecting a trial would require verifying account eligibility, usable credit and spending controls before starting services. Do not describe a trial or temporary process as an always-on deployment. The existing [Docker deployment guide](deployment.md) covers the project's persistent-data requirements independently of a particular host.
