# QuickFix / OrderFix — pharmacy order support

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

QuickFix is being connected to Med Guardian and a separate mail agent for pharmacy order lookup, cancellation, exact refund review and customer/pharmacy notifications inside ASI:One. English, Hindi and Kannada are supported.

## Current availability

**Authorized merchant, payment and mail connections are pending.** Real orders, cancellations, refunds and external emails are currently unavailable. The agent returns a connection-required notice instead of sample orders or invented payment references.

Try **show my orders**, **मेरे ऑर्डर दिखाओ** or **ನನ್ನ ಆರ್ಡರ್ ತೋರಿಸಿ**. Keep the laptop runtime online.

## Prepared workflow after connection

1. Link the conversation to a merchant-authenticated customer account.
2. Retrieve that customer's actual orders and purchased items.
3. Obtain current merchant cancellation terms and the exact refundable amount.
4. Verify the captured payment and matching provider order reference.
5. Require explicit approval of an expiring review.
6. Verify merchant cancellation before requesting the approved refund.
7. Track gateway status and separate customer/pharmacy notifications.

Live refunds are disabled by default. Public product catalogs are not customer orders or refund authorization. Uncertain actions stay pending until reconciled with their original operation reference. No prescription, purchase or medicine dispensing is performed.

For planners: preserve sender/session and exact card selections. Never infer approval or describe the connection-required notice as a completed workflow. Historical simulation chats are not proof of merchant access.

[Integration requirements](https://github.com/Deepak-18-hub/orderfix-ai/blob/main/docs/direct-merchant-integration.md)
