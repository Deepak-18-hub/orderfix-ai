# QuickFix / OrderFix — multilingual order resolution

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

QuickFix coordinates pharmacy-order cancellation, refund tracking and separate customer/pharmacy notifications inside ASI:One. OrderFix's original return-request workflow is also available. Supports English, Hindi and Kannada.

## QuickFix judging demonstration

Send **quickfix show my orders**. Choose an order card, review the pharmacy's
cancellation policy, amount, original payment destination and notification
recipients, then select **Cancel and refund**. Updated cards arrive in the same
conversation. Ask **Any update?** for status or **back** to close an unapproved
review. No order IDs or approval tokens need to be typed.

Hindi: **quickfix मेरे ऑर्डर दिखाओ**. Kannada: **quickfix ನನ್ನ ಆರ್ಡರ್ ತೋರಿಸಿ**.
Use **English**, **हिंदी**, or **ಕನ್ನಡ** to switch languages.

This deployment is a clearly labelled local simulation: a fictional pharmacy,
simulated captured payments/refunds, and captured emails that do not reach an
inbox. Distinct local uAgents exchange typed messages and persist their outcomes.
The teammate Med Guardian and mail agents, Razorpay test refunds and external
email delivery are not yet connected/verified. No real medicine or money moves.

One order is cancellable before dispatch; the dispatched order is rejected.
Approvals bind to the current order and expire. Duplicate confirmations reuse the
same operation. Order, payment, refund and notification states stay separate.
The laptop runtime and network must remain online for judging.

For planners: begin with `quickfix show my orders`. Preserve sender/session and
forward exact `qf_select`, `qf_approve`, `qf_back` or `qf_status` card selections.
Obtain explicit user approval; never infer it or suppress the simulation labels.

## Original return-request demonstration

This agent currently uses a synthetic demo store. Each conversation receives its own sample orders. Return requests are truly persisted in the agent's local database. No real purchase, return shipment or refund takes place.

1. Send `order #1001`.
2. Send `return #1001 item 1 quantity 1 damaged`.
3. Review the item, quantity, reason and policy. Use the displayed `confirm TOKEN` command or review card.
4. Send `return status #1001`.

Hindi: `ऑर्डर #1001 का आइटम 1 खराब है, वापस करना है`.
Kannada: `ಆರ್ಡರ್ #1001 ವಸ್ತು 1 ಹಾಳಾಗಿದೆ, ವಾಪಸ್ ಮಾಡಬೇಕು`.
Use `English`, `हिंदी`, or `ಕನ್ನಡ` to switch languages. Product names remain as supplied by the store.

## Behavior and limits

The agent checks a clearly labeled sample policy: requests within 14 days after fulfillment, subject to item exclusions and remaining returnable quantity. `#1002` demonstrates an expired window, `#1003` an unfulfilled order, `#1004` an excluded digital item and `#1005` an existing return. This is a demonstration policy, not any real merchant's published terms.

Approval creates a request, not an approved refund. The agent verifies tool results, prevents duplicate retries, and reconciles uncertain submissions before allowing new actions. Keep the same conversation for access to its demo orders and review tokens.

The optional Shopify connector requires merchant-authorized development-store credentials and a merchant-issued test access code. It does not support arbitrary real shoppers yet. Photos, exchanges, shipping labels, pickup bookings and money movement are outside this version.

For planners: preserve the same sender and session for the complete flow. Forward explicit card JSON (`action`, `review_token`) or exact `confirm TOKEN` / `cancel TOKEN` text after user approval. Never infer approval. Do not omit the demo/test-store label.

Keywords: QuickFix, pharmacy order cancellation, refund tracking, multi-agent coordination, captured email, return request, order tracking, Hindi, Kannada, shopping support.
