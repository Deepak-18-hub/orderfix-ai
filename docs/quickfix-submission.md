# QuickFix submission readiness

Prepared on 24 October 2026. This packet describes the new pharmacy
cancellation/refund extension. It does not reuse the earlier OrderFix return
request demo as evidence of the new workflow.

## Project description

QuickFix helps shoppers and caregivers resolve an unwanted pharmacy order after
purchase. Inside an ASI:One conversation, they select an order, inspect its
cancellation policy and refund amount, approve the specific action, and follow
separate pharmacy, refund and notification states. Persistent operation records
and typed agent replies prevent duplicate cancellation/refund attempts and keep
uncertain outcomes visible. English, Hindi and Kannada interactions are supported.

The local demonstration uses a fictional pharmacy, simulated payments and
captured customer/pharmacy emails. No medication is dispensed, no money moves and
no inbox delivery is implied. A connected Med Guardian agent, external mail
agent and Razorpay test transaction require their own verification.

## Evidence checklist

| Deliverable or claim | Current state |
| --- | --- |
| Public repository | [orderfix-ai repository](https://github.com/Deepak-18-hub/orderfix-ai); QuickFix implementation and button compatibility published through `4ed3141`, with 407 passing public tests |
| Registered OrderFix identity | Existing [Agentverse profile](https://agentverse.ai/agents/details/agent1qdhv4kkryldes33rtzvpr6uznhdup5tdz9apfthxw7r7r8jv930ccrqspns/profile); retain its private seed |
| Direct ASI:One entry | [OrderFix chat](https://asi1.ai/ai/agent1qdhv4kkryldes33rtzvpr6uznhdup5tdz9apfthxw7r7r8jv930ccrqspns); complete English QuickFix simulation verified live on 24 October 2026; start with `quickfix show my orders` |
| Teammate pharmacy integration | Source reviewed; contract compatibility and live replies unverified |
| Teammate mail integration | Being built; no configured email account or real recipients |
| Gateway test refund | No Razorpay credentials or captured provider test payment supplied |
| New shared chat | [Verified QuickFix simulation](https://asi1.ai/shared-chat/56e03728-b610-4698-b18e-8f250451fb5b): selection, policy, approval, cancellation, processed simulated refund and two captured receipts |
| New 3–5 minute video | Pending: record the new workflow and identify every simulated component |
| Official project submission | Not completed by this implementation |

List every agent actually submitted with its verified profile/address. Local
peer simulators are not the teammate's deployed agents. The published Med
Guardian addresses in the [source review](medguardian-review.md) are reference
information until ownership, protocol and availability have been checked.

## Official submission route

The [current Fetch-A-Thon 3.0 hackpack](https://www.fetch.ai/events/hackathons/fetch-a-thon-3/hackpack)
requires both online and offline participants to use the **Project Submission
Agent** on the linked **ASI:One dashboard**. Include the public repository,
complete shared-chat URL, each submitted agent's profile, demo video and project
description, then verify the returned submission record. An earlier Devpost-only
checklist does not replace this dashboard requirement.

The same hackpack asks for a **3–5 minute** demo and the Innovation Lab/hackathon
README badges. The repository retains both badges. Show the real interaction
inside ASI:One; a custom frontend is unnecessary for this primary workflow.

## Suggested four-minute demonstration

1. **0:00–0:30:** Explain the post-purchase problem, target user and simulation
   labels. Show the registered conversational entry point.
2. **0:30–1:15:** Load the authorized customer's orders and select the eligible
   one. Show the pharmacy policy and exact amount before approval.
3. **1:15–2:15:** Approve once and show actual peer requests/responses, pharmacy
   cancellation, the recorded refund state and both notification outcomes.
4. **2:15–3:15:** Repeat approval/status to demonstrate duplicate protection;
   show the dispatched-order rejection and one Hindi or Kannada interaction.
5. **3:15–4:00:** Explain persistence/recovery, show setup instructions and state
   which external integrations remain unverified.

Do not call a local refund a Razorpay refund, a captured email a delivered email,
or a gateway refund adapter native ASI Payment Protocol checkout. If external
test credentials and teammate agents become ready, replace simulation claims
only after obtaining matching provider evidence and a fresh shared chat. Keep
secrets, private recipients and active access invitations out of public evidence.
