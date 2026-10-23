# Connect a Shopify development store

OrderFix starts in `STORE_MODE=demo`, with synthetic orders in SQLite. No Shopify account, live return, refund, or shipment is implied by that mode. The Shopify connector has transport-level tests; a live development-store run is still required before claiming a verified Shopify integration.

## 1. Create the test store and app

1. Open the [Shopify Dev Dashboard](https://dev.shopify.com/), create or select your organization, then choose **Stores → Create store → Dev**. Shopify requires a Partner account or suitable merchant developer permissions. Use the development-store route, not a paid production subscription. See [Shopify's dev-store guide](https://shopify.dev/docs/apps/build/stores/development-stores).
2. Create a Dev Dashboard app in the same organization. Set its API scopes to `read_orders`, `read_returns`, and `write_returns`; release the app version and install it on your development store. If Shopify asks for customer-data access needed for the order's customer ID, complete that access setup. OrderFix does not query customer email, phone, or postal address.
3. In the app's Settings, locate the client ID and client secret. Keep these in the local `.env` or deployment secret configuration. Do not paste them into a chat or repository.
4. For an app and store in the same organization, the connector exchanges those credentials for an access token and refreshes it before expiration. Shopify's [client credentials guide](https://shopify.dev/docs/apps/build/authentication-authorization/client-credentials-grant) describes this grant and its 24-hour lifetime. It is not an authorization scheme for unrelated merchants.

The connector pins Admin GraphQL API `2026-10`. If a response advertises a different API version, it stops for administrator review.

## 2. Prepare controlled orders

Create a dedicated test customer and a physical product, such as “Demo headphones.” Create a test order attached to that customer using a test payment gateway, then mark its physical items fulfilled. Keep the order small and recent. No real payment or shipping is required for this development-store demonstration. Shopify documents the [test-order restrictions](https://shopify.dev/docs/apps/build/stores/development-stores#limitations).

Obtain the customer's exact Shopify GraphQL ID, such as `gid://shopify/Customer/123456789`, from the store's admin/API tooling. Use the customer's ID, not an email or order number, when issuing access. Guest orders with no customer ID are deliberately unavailable.

Prepare these scenarios:

- A recently fulfilled order with available return quantity.
- An unfulfilled order that the sample policy rejects.
- The first order after a return request, to demonstrate status retrieval and duplicate prevention.

The MVP applies an explicitly labelled **sample policy: 14 days after fulfillment**, excluding gift cards. It does not infer the merchant's actual policy. For multiple shipments, it uses the earliest successful fulfillment creation time, which can conservatively reject a newer item rather than extend an older item's window. Replace this with merchant-defined, per-item rules before production use.

## 3. Configure OrderFix

Create `.env` with `python -m orderfix.setup_local`, then edit it locally:

```dotenv
STORE_MODE=shopify
SHOPIFY_TEST_STORE=true
SHOPIFY_SHOP_DOMAIN=your-store.myshopify.com
SHOPIFY_CLIENT_ID=YOUR_APP_CLIENT_ID
SHOPIFY_CLIENT_SECRET=YOUR_APP_CLIENT_SECRET
SHOPIFY_ACCESS_TOKEN=
ORDERFIX_DB=data/orderfix.sqlite3
```

Alternatively, supply an existing authorized Admin API access token in `SHOPIFY_ACCESS_TOKEN` and leave both client credential values empty. An expiring static token must be replaced by the operator. The preferred same-organization credentials route handles renewal automatically. The domain must be a `myshopify.com` hostname without `https://` or a path.

The Shopify plugin, if installed in Codex, does not replace the deployed agent's credentials. No Shopify plugin is required by this Python connector.

## 4. Issue access and verify the workflow

Run this operator command from the project directory using the same `ORDERFIX_DB` as the running agent:

```shell
python -m orderfix.invite gid://shopify/Customer/123456789
```

Give its short-lived access command only to the controlled test customer. For local testing, start:

```shell
python -m orderfix.cli --mode shopify --db data/orderfix.sqlite3
```

In the conversation, enter the generated `access ...` command, then `order #YOUR_ORDER_NUMBER`, `return #YOUR_ORDER_NUMBER item 1 quantity 1 damaged`, and the exact confirmation command shown in the review. Finally, request `return status #YOUR_ORDER_NUMBER`.

Inspect the return in Shopify admin and compare its ID/name and status to the agent's response. The connector uses [`returnRequest`](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/returnRequest), whose initial status is `REQUESTED` and requires merchant review. It does not approve returns, issue refunds, purchase labels, arrange pickup, or send customer emails. A later status is reported as Shopify records it; a return status is not proof of a refund.

The local CLI is a test harness. Repeat the same workflow in the registered agent's ASI:One chat before recording the challenge demonstration. Access codes are for controlled testing, not production customer authentication. Avoid sharing a chat containing a still-valid access code.

## Reliability and limits

- The connector validates the order's customer ID on lookup and reconciliation. An order number alone never grants access.
- It queries [`returnableFulfillments`](https://shopify.dev/docs/api/admin-graphql/2026-10/queries/returnableFulfillments) immediately before writing, so the mutation uses current fulfillment-line IDs and quantities. A split shipment may contribute several fulfillment lines to one requested order item.
- Reads have a 15-second overall request limit and at most one retry on transient failure. Mutations are never automatically retried.
- Each request includes an operation marker in the line's `customerNote`. A fresh read checks this marker, return ID, line, reason, and quantity before reporting success. The [`ReturnLineItem` schema](https://shopify.dev/docs/api/admin-graphql/2026-10/objects/ReturnLineItem) exposes that note. The marker is an application reconciliation mechanism, not Shopify-enforced idempotency. Run a single OrderFix instance with its persistent SQLite operation journal.
- An ambiguous result stays pending for reconciliation; it must never trigger another blind submission. Merchant review is required if verification remains unavailable.
- Limits are 100 order items, fewer than 20 fulfillments, 10 returns, 20 lines per return, and 10 returnable fulfillments with 20 lines each. Every paginated response must explicitly report `hasNextPage=false`. Larger, incomplete, and unverified return data is rejected for merchant review.
- The pinned schema still supports the deprecated `returnReason` enum used here. Plan a migration to `returnReasonDefinitionId` before moving to an API version that removes the enum.

Run `python -m pytest tests/test_shopify.py -q` for the mock transport suite. Passing these tests demonstrates connector behavior against simulated responses, not live Shopify execution.
