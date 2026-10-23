"""Transport-level contract tests. These do not call a live Shopify store."""
from __future__ import annotations

import asyncio
import copy
from functools import wraps
import json
from datetime import datetime, timezone

import httpx
import pytest

from orderfix.models import AmbiguousAction, LineItem, Order, ProviderError
from orderfix.shopify import ShopifyProvider

CUSTOMER = "gid://shopify/Customer/1"
ORDER_ID = "gid://shopify/Order/10"
LINE_ID = "gid://shopify/LineItem/20"
FULFILLMENT_LINE_ID = "gid://shopify/FulfillmentLineItem/30"
RETURN_ID = "gid://shopify/Return/40"


def conn(nodes, more=False):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": more}}


def raw_order():
    return {"id": ORDER_ID, "name": "#1001", "customer": {"id": CUSTOMER},
        "displayFulfillmentStatus": "FULFILLED",
        "totalPriceSet": {"presentmentMoney": {"amount": "999.00", "currencyCode": "INR"}},
        "fulfillments": [{"status": "SUCCESS", "createdAt": "2026-10-20T10:00:00Z"}],
        "lineItems": conn([{"id": LINE_ID, "title": "Headphones", "quantity": 2, "isGiftCard": False}]),
        "returns": conn([])}


def raw_return(operation="op-123", quantity=1, line_id=LINE_ID):
    return {"id": RETURN_ID, "name": "#1001-R1", "status": "REQUESTED", "createdAt": "2026-10-23T10:00:00Z",
        "returnLineItems": conn([{"id": "gid://shopify/ReturnLineItem/50", "quantity": quantity,
            "returnReason": "DEFECTIVE", "customerNote": f"[orderfix:{operation};reason:damaged]",
            "fulfillmentLineItem": {"lineItem": {"id": line_id}}}])}


def sample_order():
    return Order(id=ORDER_ID, number="#1001", customer_ref=CUSTOMER, fulfillment_status="FULFILLED",
        fulfilled_at=datetime(2026, 10, 20, tzinfo=timezone.utc), currency="INR", total="999.00",
        lines=[LineItem(id=LINE_ID, title="Headphones", quantity=2, returnable_quantity=2)], mode="shopify")


class Store:
    def __init__(self):
        self.order = raw_order()
        self.calls = []
        self.mutations = []
        self.fault = None
        self.returnable = conn([{"returnableFulfillmentLineItems": conn([
            {"quantity": 2, "fulfillmentLineItem": {"id": FULFILLMENT_LINE_ID, "lineItem": {"id": LINE_ID}}}
        ])}])

    def handle(self, request):
        assert request.url.host == "orderfix-test.myshopify.com"
        assert request.url.path == "/admin/api/2026-10/graphql.json"
        assert request.headers["X-Shopify-Access-Token"] == "test-secret"
        payload = json.loads(request.content)
        query, variables = payload["query"], payload["variables"]
        name = query.split("(")[0].strip()
        self.calls.append(name)
        if self.fault:
            response = self.fault(request, query)
            if response:
                return response
        if "OrderFixSearch" in query:
            data = {"orders": conn([{k: self.order[k] for k in ("id", "name", "customer")}])}
        elif "OrderFixOrder" in query or "OrderFixReturns" in query:
            data = {"order": copy.deepcopy(self.order)}
        elif "OrderFixReturnable" in query:
            data = {"returnableFulfillments": copy.deepcopy(self.returnable)}
        elif "OrderFixRequest" in query:
            self.mutations.append(variables)
            items = variables["input"]["returnLineItems"]
            result = raw_return(quantity=sum(i["quantity"] for i in items))
            result["returnLineItems"]["nodes"][0]["customerNote"] = items[0]["customerNote"]
            self.order["returns"] = conn([result])
            data = {"returnRequest": {"return": {"id": RETURN_ID}, "userErrors": []}}
        else:
            raise AssertionError(query)
        return httpx.Response(200, json={"data": data}, headers={"X-Shopify-API-Version": "2026-10"})


def run_async(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return wrapped


@pytest.fixture
def store_provider():
    store = Store()
    client = httpx.AsyncClient(transport=httpx.MockTransport(store.handle))
    yield store, ShopifyProvider("orderfix-test.myshopify.com", "test-secret", client=client)
    asyncio.run(client.aclose())


@run_async
async def test_order_lookup_uses_exact_customer_and_minimal_data(store_provider):
    store, provider = store_provider
    order = await provider.get_order("1001", CUSTOMER)
    assert order and order.lines[0].returnable_id == FULFILLMENT_LINE_ID
    assert order.lines[0].returnable_quantity == 2 and order.mode == "shopify"
    assert order.customer_ref == CUSTOMER
    assert "email" not in order.model_dump() and "address" not in order.model_dump()
    assert await provider.get_order("1001", "gid://shopify/Customer/2") is None
    assert store.calls[-1] == "query OrderFixSearch"
    previous = len(store.calls)
    assert await provider.get_order('1001" OR status:any', CUSTOMER) is None
    assert await provider.get_order("1001", "someone@example.com") is None
    assert len(store.calls) == previous


@run_async
async def test_earliest_successful_fulfillment_is_used(store_provider):
    store, provider = store_provider
    store.order["fulfillments"] += [
        {"status": "SUCCESS", "createdAt": "2026-10-01T10:00:00Z"},
        {"status": "CANCELLED", "createdAt": "2026-09-01T10:00:00Z"}]
    order = await provider.get_order("1001", CUSTOMER)
    assert order.fulfilled_at == datetime(2026, 10, 1, 10, tzinfo=timezone.utc)


@run_async
@pytest.mark.parametrize("part", ["lineItems", "returns", "returnable", "returnable_lines", "return_lines", "fulfillments"])
async def test_truncated_data_is_rejected(store_provider, part):
    store, provider = store_provider
    if part in ("lineItems", "returns"):
        store.order[part]["pageInfo"]["hasNextPage"] = True
    elif part == "returnable":
        store.returnable["pageInfo"]["hasNextPage"] = True
    elif part == "returnable_lines":
        store.returnable["nodes"][0]["returnableFulfillmentLineItems"]["pageInfo"]["hasNextPage"] = True
    elif part == "return_lines":
        item = raw_return()
        item["returnLineItems"]["pageInfo"]["hasNextPage"] = True
        store.order["returns"] = conn([item])
    else:
        store.order["fulfillments"] *= 20
    with pytest.raises(ProviderError):
        await provider.get_order("1001", CUSTOMER)
    assert not store.mutations


@run_async
async def test_return_uses_fulfillment_id_reads_back_and_deduplicates(store_provider):
    store, provider = store_provider
    record = await provider.request_return(sample_order(), LINE_ID, 1, "damaged", "op-123")
    assert record.id == RETURN_ID and record.status == "REQUESTED"
    assert record.operation_id == "op-123"
    sent = store.mutations[0]["input"]
    assert sent["orderId"] == ORDER_ID
    assert sent["returnLineItems"][0]["fulfillmentLineItemId"] == FULFILLMENT_LINE_ID
    assert sent["returnLineItems"][0]["returnReason"] == "DEFECTIVE"
    assert store.calls[-1] == "query OrderFixReturns"
    duplicate = await provider.request_return(sample_order(), LINE_ID, 1, "damaged", "op-123")
    assert duplicate == record and len(store.mutations) == 1


@run_async
async def test_split_fulfillment_allocates_only_requested_quantity(store_provider):
    store, provider = store_provider
    lines = store.returnable["nodes"][0]["returnableFulfillmentLineItems"]["nodes"]
    lines[0]["quantity"] = 1
    lines.append({"quantity": 3, "fulfillmentLineItem": {"id": "gid://shopify/FulfillmentLineItem/31", "lineItem": {"id": LINE_ID}}})
    result = await provider.request_return(sample_order(), LINE_ID, 2, "damaged", "op-123")
    assert result.quantity == 2
    assert [x["quantity"] for x in store.mutations[0]["input"]["returnLineItems"]] == [1, 1]


@run_async
async def test_changed_returnable_quantity_rejects_before_write(store_provider):
    store, provider = store_provider
    store.returnable = conn([])
    with pytest.raises(ProviderError, match="no longer"):
        await provider.request_return(sample_order(), LINE_ID, 1, "damaged", "op-123")
    assert not store.mutations


@run_async
async def test_mutation_timeout_is_ambiguous_and_never_retried(store_provider):
    store, provider = store_provider
    def fault(request, query):
        if "OrderFixRequest" in query:
            raise httpx.ReadTimeout("test-secret must not escape", request=request)
    store.fault = fault
    with pytest.raises(AmbiguousAction) as exc:
        await provider.request_return(sample_order(), LINE_ID, 1, "damaged", "op-123")
    assert "test-secret" not in str(exc.value)
    assert store.calls.count("mutation OrderFixRequest") == 1


@run_async
async def test_unverified_success_is_ambiguous(store_provider):
    store, provider = store_provider
    def fault(request, query):
        if "OrderFixRequest" in query:
            return httpx.Response(200, json={"data": {"returnRequest": {"return": {"id": RETURN_ID}, "userErrors": []}}})
    store.fault = fault
    with pytest.raises(AmbiguousAction, match="could not be verified"):
        await provider.request_return(sample_order(), LINE_ID, 1, "damaged", "op-123")
    assert store.calls.count("mutation OrderFixRequest") == 1


@run_async
async def test_user_error_is_known_failure_without_raw_message(store_provider):
    store, provider = store_provider
    def fault(request, query):
        if "OrderFixRequest" in query:
            return httpx.Response(200, json={"data": {"returnRequest": {"return": None,
                "userErrors": [{"code": "INVALID", "message": "private details test-secret"}]}}})
    store.fault = fault
    with pytest.raises(ProviderError) as exc:
        await provider.request_return(sample_order(), LINE_ID, 1, "damaged", "op-123")
    assert not isinstance(exc.value, AmbiguousAction) and "test-secret" not in str(exc.value)
    assert store.calls.count("mutation OrderFixRequest") == 1


@run_async
async def test_read_retries_once_and_mutation_is_not_involved(store_provider):
    store, provider = store_provider
    store.fault = lambda request, query: httpx.Response(503, text="private test-secret")
    with pytest.raises(ProviderError) as exc:
        await provider.get_order("1001", CUSTOMER)
    assert len(store.calls) == 2 and "test-secret" not in str(exc.value)


@run_async
async def test_reconciliation_is_customer_scoped(store_provider):
    store, provider = store_provider
    store.order["customer"]["id"] = "gid://shopify/Customer/2"
    store.order["returns"] = conn([raw_return()])
    with pytest.raises(ProviderError):
        await provider.find_return(sample_order(), "op-123")
    with pytest.raises(ProviderError):
        await provider.request_return(sample_order(), LINE_ID, 1, "damaged", "op-123")
    assert not store.mutations


@run_async
async def test_existing_operation_payload_mismatch_never_writes(store_provider):
    store, provider = store_provider
    store.order["returns"] = conn([raw_return(quantity=2)])
    with pytest.raises(AmbiguousAction):
        await provider.request_return(sample_order(), LINE_ID, 1, "damaged", "op-123")
    assert not store.mutations


@run_async
async def test_client_credentials_cache_and_refresh_before_expiry(monkeypatch):
    now, token_calls, api_calls = [1000.0], [], []
    monkeypatch.setattr("orderfix.shopify.time.monotonic", lambda: now[0])
    def handle(request):
        if request.url.path == "/admin/oauth/access_token":
            token_calls.append(request)
            assert b"grant_type=client_credentials" in request.content
            return httpx.Response(200, json={"access_token": f"token-{len(token_calls)}", "expires_in": 86399})
        api_calls.append(request)
        return httpx.Response(200, json={"data": {"orders": conn([])}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = ShopifyProvider("orderfix-test.myshopify.com", client_id="id", client_secret="secret", client=client)
        await provider.get_order("1001", CUSTOMER)
        await provider.get_order("1001", CUSTOMER)
        assert len(token_calls) == 1
        now[0] += 86350
        await provider.get_order("1001", CUSTOMER)
        assert len(token_calls) == 2
        assert api_calls[-1].headers["X-Shopify-Access-Token"] == "token-2"
        provider.close()
        assert not client.is_closed


@pytest.mark.parametrize("domain", ["https://store.myshopify.com", "store.myshopify.com.evil.com", "127.0.0.1", "store.myshopify.com/path"])
def test_endpoint_is_allowlisted(domain):
    with pytest.raises(ValueError):
        ShopifyProvider(domain, "secret")
