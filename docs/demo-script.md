# OrderFix demonstration script

## State the environment clearly

Opening line: **“OrderFix turns a shopping complaint into a verified return request in English, Hindi, or Kannada. This demonstration uses synthetic orders and a persistent local store. It does not make purchases, issue refunds, or claim a live Shopify transaction.”**

The four supported workflows are order lookup, return eligibility, confirmed return submission, and return-status retrieval. A submitted request still requires merchant approval. An `OPEN` or `CLOSED` return status alone does not establish that money was refunded.

The terminal harness is useful for rehearsal. It is **not** proof of Agentverse registration, ACP interoperability, ASI:One discovery, or a completed ASI:One conversation. Record the final demonstration inside ASI:One after those steps are completed and tested.

## Rehearse locally

From the repository directory, with dependencies installed and the virtual environment active:

```powershell
python -m orderfix.cli --scripted --language en --db data/demo-en.sqlite3
python -m orderfix.cli --scripted --language hi --db data/demo-hi.sqlite3
python -m orderfix.cli --scripted --language kn --db data/demo-kn.sqlite3
```

Each scripted run uses a new conversation unless `--session` is specified. Its confirmations are automatic and restricted to local synthetic demo mode. This is a test harness, not the user consent mechanism in ASI:One.

For a manual conversation:

```powershell
python -m orderfix.cli --language en --session rehearsal-1 --db data/rehearsal.sqlite3
```

Reusing the same database and session resumes its state. A new session receives its own synthetic customer orders; use one to start a clean demonstration without deleting evidence.

## Four-minute recording

Target four minutes to fit the event's three-to-five-minute video requirement.

| Time | Action | What the viewer should see |
|---|---|---|
| 0:00–0:20 | State the customer problem and the four workflows. | A concrete shopping problem and the clear synthetic-store disclosure. |
| 0:20–0:35 | Show the real Agentverse profile and invoke the agent in ASI:One. | The registered agent and its working conversational entry point. |
| 0:35–1:00 | Send `order #1001`. | Fulfillment state, two items, available quantities, and an explicit demo banner. |
| 1:00–1:30 | Send `return #1001 item 1 quantity 1 damaged`. | A policy check and a review with order, item, quantity, reason, and a fresh review token. Explain that no submission has occurred yet. |
| 1:30–1:55 | Use the confirmation card if available, or type the exact `confirm TOKEN` shown. | A verified return reference and its current state. Copy the actual token; never reuse the example word `TOKEN`. |
| 1:55–2:20 | Repeat the same `confirm TOKEN`, then send `return status #1001`. | The same existing request is recovered, and current persisted status is read. No duplicate request appears. |
| 2:20–2:40 | Send `return #1002 item 1 damaged`. | An explanation that the sample 14-day policy window has expired. |
| 2:40–3:15 | Send `हिंदी` and `return status #1001`; then send `ಕನ್ನಡ` and repeat the status request. | Hindi and Kannada transaction templates with the same machine-readable return reference. |
| 3:15–3:40 | Explain the operation journal, exact confirmation, and error recovery. Show the recorded automated-test result if useful. | How a pending action is reconciled rather than blindly resent; distinguish automated failure tests from a live outage demonstration. |
| 3:40–4:00 | Show the repository and explain the architecture and continuation plan. | ACP → intent → deterministic policy and review → merchant provider → persisted verification. Mention optional Shopify test-store integration only at its actual tested level. End with the verified return outcome. |

If interactive cards fail to render in the target client, the text confirmation remains sufficient. Explain the actual client behavior instead of displaying a mock card as a working integration.

## Multilingual prompts

Use a fresh session if you want to submit the same sample return separately in each language.

| Language | Choose language | Ask to return a damaged item |
|---|---|---|
| English | `English` or `en` | `return #1001 item 1 quantity 1 damaged` |
| Hindi | `हिंदी` or `hi` | `ऑर्डर #1001 का आइटम 1 खराब है, वापस करना है` |
| Kannada | `ಕನ್ನಡ` or `kn` | `ಆರ್ಡರ್ #1001 ವಸ್ತು 1 ಹಾಳಾಗಿದೆ, ವಾಪಸ್ ಮಾಡಬೇಕು` |

You can also use Unicode digits, for example `ऑर्डर १००१` and `ಆರ್ಡರ್ ೧೦೦೧`. Language support is deterministic for the documented vocabulary and the fixed response templates. An optional ASI1 key can help classify unfamiliar phrasing; no key is required for these workflows. Do not promise unrestricted language understanding.

## Additional useful checks

- `return #1003 item 1 damaged`: not fulfilled yet.
- `return #1004 item 1 changed_mind`: excluded digital item.
- `return status #1005`: a previously seeded request.
- `return #1005 item 1 damaged`: no quantity remains available for another return.
- `return #1001 item 1 quantity 999 damaged`: invalid input; quantity must not silently become one.
- `cancel TOKEN`: discard the pending review. This does not cancel an already submitted merchant return.
- `review`: show the pending review again.

The no-cost default is a synthetic local merchant backend. Demonstrating a connected Shopify development store is optional and requires its own authorized credentials, eligible fulfilled orders, and a merchant-issued test-customer access code. Show its real returned reference and label it as a **test store**. Never show API keys, app secrets, agent seeds, access codes, customer addresses, or email inboxes in a recording.

## Capture submission evidence

Save the ASI:One shared chat URL, each Agentverse profile URL, the public GitHub repository URL, and the video URL. Check every link in a signed-out browser where appropriate. Keep the agent process running and its database available while judges use it.
