# OrderFix — submission draft

Complete the evidence fields only after verifying each deployed result. This document is a draft and does not establish that the project is registered, published, discoverable, or submitted.

## Name and one-line pitch

**OrderFix: from shopping frustration to a verified return request, inside ASI:One.**

## Problem

Returning an online purchase often requires finding the order, interpreting store rules, choosing the right item, and repeatedly asking support what happened. Customers can mistake a conversation for a completed request, while stores handle the same lookup and policy questions repeatedly.

## Target users

Customers who want help with an order or return in English, Hindi, or Kannada, and small merchants who want a guided after-sales workflow with explicit customer approval.

## What it does

OrderFix supports four connected workflows entirely through conversation: order lookup, return-eligibility evaluation, return-request submission after a review and explicit confirmation, and retrieval of the recorded request status. The workflow checks the selected item and quantity against policy, presents the details for review, creates a persistent request, and verifies the returned record. Repeated confirmation reconciles the existing operation instead of blindly creating another request.

The default demonstration uses synthetic orders and a persistent SQLite merchant backend. Requests are real local writes, clearly identified as demo data. No purchases, refunds, parcel pickup, or shipment of goods occur. An optional Shopify adapter is intended for an authorized development store and controlled test customers; report it as live-tested only after testing against that store.

## Why it goes beyond chat

The conversation gathers missing details, selects an order and item, applies policy constraints, creates a bounded review, waits for explicit confirmation, calls a merchant provider, persists the result, and reads status back. Eligibility and transaction state come from deterministic code and stored records. A model cannot grant access, approve an action, invent a return reference, or report an unverified successful mutation.

## Languages and architecture

- Python and uAgents with an Agent Chat Protocol entry point.
- Persistent sessions, local merchant data, operation journal, and message replay protection.
- English, Hindi, and Kannada intent rules and fixed transaction-response templates.
- Optional ASI1 intent classification for unfamiliar phrasing; the documented workflows run without an API key.
- Interactive review content where supported, with an exact text-confirmation path.
- Optional Shopify test-store provider with merchant-controlled customer access.

The MVP deliberately uses one coordinating agent. Its tool orchestration and verified state changes are the core demonstration; do not claim multi-agent collaboration, Payment Protocol integration, or universal retailer support.

## Credible continuation

A merchant subscription could fund deployment and additional store integrations. Before serving real shoppers, the next development phase needs proper customer identity verification/account linking, merchant-specific return policies, operational monitoring, privacy and retention controls, and tested production merchant permissions. The MVP does not implement billing or payment handling.

## Evidence checklist

| Required evidence | Value to complete after verification |
|---|---|
| ASI:One shared chat showing all four workflows | **PENDING — paste verified shared-chat URL** |
| Agentverse profile for each submitted agent | **PENDING — paste actual profile URL** |
| Public GitHub repository | [Deepak-18-hub/orderfix-ai](https://github.com/Deepak-18-hub/orderfix-ai) |
| Short demonstration video | **PENDING — paste uploaded video URL** |
| Setup and test instructions | README in the public repository; confirm a clean setup works |
| Live ASI:One discovery and interaction | **PENDING — record the tested discovery phrase and result** |
| Runtime during judging | **PENDING — record operator, deployment location, and availability** |

## Validation record to fill in

```text
Commit:
Test command and result:
English complete workflow:
Hindi complete workflow:
Kannada complete workflow:
Expired-order rejection:
Excluded-item rejection:
Repeated confirmation / no duplicate:
Actual ASI:One conversation:
Store mode used in final demonstration:
Shopify live test performed: yes / no
Known limitations:
```

## Suggested short Devpost description

OrderFix helps customers resolve online-shopping return requests inside ASI:One. It understands the order and problem, checks the store policy, presents the exact item and quantity for approval, submits the request, and retrieves its recorded status. English, Hindi, and Kannada templates keep the outcome understandable, while explicit confirmations and operation recovery prevent accidental or duplicate submissions. Our demonstration uses clearly labelled synthetic orders with persistent local return records; no money moves. The project also includes an optional authorized Shopify development-store integration.

Before submitting, adjust the description to reflect the features actually demonstrated in the shared ASI:One chat. Do not replace pending evidence with an invented URL or describe local-only verification as a completed platform integration.
