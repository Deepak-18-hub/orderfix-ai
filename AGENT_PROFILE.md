# OrderFix — multilingual shopping support

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

OrderFix helps shoppers look up orders, check return eligibility, submit a reviewed return request, and check its status in ASI:One. Supports English, Hindi and Kannada text.

## Try the public demonstration

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

Keywords: online order support, return request, damaged item, order tracking, Hindi shopping support, Kannada shopping support, ecommerce support.
