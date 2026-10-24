# QuickFix pharmacy cancellation demonstration

QuickFix extends the existing OrderFix project with an order cancellation and refund workflow. The primary interaction starts from an already paid test order: choose the order inside ASI:One, review the pharmacy's cancellation decision and refund amount, approve once, and inspect the resulting case and notification states. Users do not need to copy order numbers, review tokens, or payment references.

## What the demonstration proves

The interface supports English, Hindi, and Kannada. It uses an order carousel, an explicit cancellation/refund review, and a status detail card. The text fallback uses product names and contextual commands such as `confirm`, `back`, and `check status`.

The engine must bind an approval to the authenticated customer's current, unexpired review. A parsed confirmation alone is not authorization to perform an arbitrary operation. Card references remain inside hidden selections; changing an order, amount, or eligibility invalidates the previous review.

The three integration modes are displayed independently:

| Component | Local demonstration | External integration |
| --- | --- | --- |
| Payment gateway | Simulated payment/refund ledger; no gateway transaction | `razorpay_test`: actual gateway sandbox API operations, only after credentials and transaction verification work |
| Pharmacy | Local MedGuardian peer simulator | Connected teammate agent, only after its identity, schema, and replies are validated |
| Notifications | Customer and pharmacy messages captured locally | Connected mail agent; provider acceptance and confirmed delivery are distinct states |

At initial implementation, the external gateway flow and teammate connections are **not verified**. Local pharmacy responses are simulations. Captured messages are not external emails. A test-mode label describes the configured mode; only verified provider results establish that a specific payment or refund occurred. No real-money purchase, charge, or refund is part of this demonstration.

## Walkthrough

Run `python -m orderfix.commerce.cli demo` for a bounded local replay and evidence
file. To keep the existing registered ASI entry running with local simulator
peers, follow `python -m orderfix.commerce.judge` in the [setup guide](quickfix-setup.md).
The latter preserves the existing identity and forces all business services to
simulation/capture. A live ASI recording of this extension is still pending.

1. Start with an authorized test customer and a prepared paid test order. For the external gateway demonstration, prepare the payment through the gateway's supported sandbox payment flow and verify that it was captured before beginning the ASI conversation.
2. Say **show my orders**, **मेरे ऑर्डर दिखाओ**, or **ನನ್ನ ಆರ್ಡರ್ ತೋರಿಸಿ**. Select the product card, or use its product name if cards are unavailable.
3. Read the cancellation review: selected order, pharmacy, refund amount, original payment method, applicable terms, and any displayed notification recipients. Select **Cancel and refund** to authorize those exact details. **Go back** closes the review; it does not cancel a submitted order.
4. Inspect the status card. Pharmacy cancellation, payment, refund, customer notification, and pharmacy notification must be reported independently. A pending refund is not a completed refund. A failed email must not trigger a second cancellation or refund.
5. Ask **check status**, **स्थिति देखें**, or **ಸ್ಥಿತಿ ಪರಿಶೀಲಿಸಿ** to refresh the same case. Try the same approval again: the engine should return the existing result, not execute another refund.

For the text fallback, exact `confirm`, `पुष्टि करें`, or `ದೃಢೀಕರಿಸಿ` approves only the current engine-bound review. Negated, ambiguous, or expanded statements such as “do not confirm,” “confirm later,” or “please approve this” do not grant approval. A fresh review is required if the previous one expired or changed.

## Evidence to collect before claiming an external integration

- The gateway's captured test payment and corresponding verified test refund reference, with credentials and private customer information omitted.
- The MedGuardian agent address and matching business response, rather than an email receipt standing in for pharmacy acceptance.
- One customer and one pharmacy notification result, distinguishing local capture, provider submission, and confirmed delivery.
- A repeated approval and a restart/status check showing the same case and refund.
- A clean ASI:One shared conversation covering order selection, review, approval, and the verified outcome.

Do not use an external checkout page as an unacknowledged part of the primary chat workflow. New refill checkout is secondary and should only be claimed as an in-chat workflow after native ASI payment compatibility is verified. A refund API call is not, by itself, Agent Payment Protocol integration.

The cards follow the [official ASI predefined card schemas](https://docs.agentverse.ai/documentation/advanced-usages/predefined-card-schemas). No custom frontend is required for this cancellation flow.
