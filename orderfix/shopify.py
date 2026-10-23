"""Small, customer-scoped Shopify Admin GraphQL connector (2026-10).

Only returnRequest writes to Shopify. It never approves, refunds, or ships.
Oversized connections fail closed; every mutation is followed by a fresh read.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from typing import Any, Literal

import httpx

from .models import AmbiguousAction, LineItem, Order, ProviderError, Reason, ReturnRecord

API_VERSION = "2026-10"
_REASONS = {
    "damaged": "DEFECTIVE", "wrong_item": "WRONG_ITEM", "size": "OTHER",
    "changed_mind": "UNWANTED", "other": "OTHER",
}
_MARKER = re.compile(r"\[orderfix:([A-Za-z0-9_-]{1,100});reason:(damaged|wrong_item|size|changed_mind|other)\]")
_RETURN_FIELDS = """
  id name status createdAt
  returnLineItems(first: 20) {
    pageInfo { hasNextPage }
    nodes { id quantity ... on ReturnLineItem {
      customerNote returnReason fulfillmentLineItem { lineItem { id } }
    } }
  }
"""
_SEARCH = """query OrderFixSearch($query: String!) {
  orders(first: 10, query: $query) {
    pageInfo { hasNextPage }
    nodes { id name customer { id } }
  }
}"""
_ORDER = """query OrderFixOrder($id: ID!) {
  order(id: $id) {
    id name customer { id } displayFulfillmentStatus
    totalPriceSet { presentmentMoney { amount currencyCode } }
    fulfillments(first: 20) { status createdAt }
    lineItems(first: 100) {
      pageInfo { hasNextPage }
      nodes { id title quantity isGiftCard }
    }
    returns(first: 10) { pageInfo { hasNextPage } nodes {
""" + _RETURN_FIELDS + """ } }
  }
}"""
_RETURNS = """query OrderFixReturns($id: ID!) {
  order(id: $id) { id customer { id }
    returns(first: 10) { pageInfo { hasNextPage } nodes {
""" + _RETURN_FIELDS + """ } }
  }
}"""
_RETURNABLE = """query OrderFixReturnable($id: ID!) {
  returnableFulfillments(orderId: $id, first: 10) {
    pageInfo { hasNextPage }
    nodes { returnableFulfillmentLineItems(first: 20) {
      pageInfo { hasNextPage }
      nodes { quantity fulfillmentLineItem { id lineItem { id } } }
    } }
  }
}"""
_REQUEST = """mutation OrderFixRequest($input: ReturnRequestInput!) {
  returnRequest(input: $input) {
    return { id }
    userErrors { code field }
  }
}"""


def _nodes(connection: dict[str, Any]) -> list[dict[str, Any]]:
    # Do not accidentally treat a missing/unknown pageInfo as a complete list.
    if connection.get("pageInfo", {}).get("hasNextPage") is not False:
        raise ProviderError("This order needs merchant review because its data is incomplete or too large.")
    nodes = connection.get("nodes")
    if not isinstance(nodes, list):
        raise ProviderError("Shopify returned incomplete order data.")
    return nodes


def _date(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Missing timezone")
    return result


class ShopifyProvider:
    mode: Literal["shopify"] = "shopify"

    def __init__(self, domain: str, access_token: str | None = None, *,
                 client_id: str | None = None, client_secret: str | None = None,
                 client: httpx.AsyncClient | None = None):
        domain = domain.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", domain):
            raise ValueError("Shopify domain must be a store.myshopify.com hostname, without a URL path.")
        if bool(client_id) != bool(client_secret):
            raise ValueError("Supply both Shopify client ID and client secret.")
        if not access_token and not (client_id and client_secret):
            raise ValueError("Shopify credentials are not configured.")
        self.domain = domain
        self._token = access_token
        self._client_id, self._client_secret = client_id, client_secret
        self._expires_at = float("inf") if access_token else 0.0
        self._client = client
        self._token_lock = asyncio.Lock()

    def close(self) -> None:
        """Injected clients belong to the caller; owned clients close per request."""

    async def _post(self, path: str, **kwargs: Any) -> httpx.Response:
        kwargs["timeout"] = httpx.Timeout(12.0, connect=5.0)
        kwargs["follow_redirects"] = False
        url = f"https://{self.domain}{path}"
        # Bound the entire request, including streaming responses, to 15 seconds.
        async with asyncio.timeout(15):
            if self._client is not None:
                return await self._client.post(url, **kwargs)
            async with httpx.AsyncClient() as client:
                return await client.post(url, **kwargs)

    async def _access_token(self) -> str:
        async with self._token_lock:
            if self._token and time.monotonic() < self._expires_at:
                return self._token
            if not self._client_id or not self._client_secret:
                raise ProviderError("Shopify authentication needs administrator attention.")
            try:
                response = await self._post("/admin/oauth/access_token", data={
                    "grant_type": "client_credentials", "client_id": self._client_id,
                    "client_secret": self._client_secret,
                })
                response.raise_for_status()
                data = response.json()
                token, expiry = data["access_token"], int(data["expires_in"])
                if not isinstance(token, str) or not token or expiry <= 0:
                    raise ValueError("Invalid token response")
            except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError):
                raise ProviderError("Shopify authentication needs administrator attention.") from None
            self._token = token
            self._expires_at = time.monotonic() + max(0, expiry - 60)
            return token

    async def _graphql(self, query: str, variables: dict[str, Any], *, mutation: bool = False) -> dict[str, Any]:
        for attempt in range(1 if mutation else 2):
            token = await self._access_token()  # Authentication fails before dispatch.
            try:
                response = await self._post(f"/admin/api/{API_VERSION}/graphql.json",
                    json={"query": query, "variables": variables},
                    headers={"X-Shopify-Access-Token": token})
            except (httpx.HTTPError, TimeoutError):
                if mutation:
                    raise AmbiguousAction("The return may have been submitted. Check its status before trying again.") from None
                if attempt == 0:
                    await asyncio.sleep(0.15)
                    continue
                raise ProviderError("Shopify is temporarily unavailable. Please try the lookup again.") from None
            if response.status_code in (401, 403):
                if response.status_code == 401 and not mutation and attempt == 0 and self._client_id:
                    self._token, self._expires_at = None, 0
                    continue
                raise ProviderError("Shopify access needs administrator attention.")
            if response.status_code == 429 or response.status_code >= 500:
                if mutation:
                    raise AmbiguousAction("Shopify did not confirm the return. Check its status before trying again.")
                if attempt == 0:
                    await asyncio.sleep(0.15)
                    continue
                raise ProviderError("Shopify is busy. Please try the lookup again.")
            if response.status_code != 200:
                error = AmbiguousAction if mutation else ProviderError
                raise error("Shopify could not confirm this operation.")
            actual_version = response.headers.get("X-Shopify-API-Version")
            if actual_version and actual_version != API_VERSION:
                error = AmbiguousAction if mutation else ProviderError
                raise error("Shopify API version differs from the configured version; administrator review is required.")
            try:
                body = response.json()
                errors = body.get("errors", [])
                if errors:
                    throttled = any(e.get("extensions", {}).get("code") == "THROTTLED" for e in errors)
                    if throttled and not mutation and attempt == 0:
                        await asyncio.sleep(0.15)
                        continue
                    error = AmbiguousAction if mutation else ProviderError
                    raise error("Shopify could not confirm this operation; check its status before submitting again.")
                if not isinstance(body.get("data"), dict):
                    raise ValueError("Missing data")
                return body["data"]
            except (ValueError, TypeError, AttributeError):
                error = AmbiguousAction if mutation else ProviderError
                raise error("Shopify returned an incomplete response.") from None
        raise ProviderError("Shopify is temporarily unavailable.")

    @staticmethod
    def _customer_matches(raw: dict[str, Any] | None, customer_ref: str) -> bool:
        return bool(raw and (raw.get("customer") or {}).get("id") == customer_ref)

    async def _returnable(self, order_id: str) -> dict[str, list[tuple[str, int]]]:
        data = await self._graphql(_RETURNABLE, {"id": order_id})
        result: dict[str, list[tuple[str, int]]] = {}
        try:
            for fulfillment in _nodes(data["returnableFulfillments"]):
                for item in _nodes(fulfillment["returnableFulfillmentLineItems"]):
                    line = item["fulfillmentLineItem"]
                    quantity = int(item["quantity"])
                    if quantity < 0:
                        raise ValueError("Negative quantity")
                    result.setdefault(line["lineItem"]["id"], []).append((line["id"], quantity))
            return result
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ProviderError("Shopify returned incomplete fulfillment data.") from None

    @staticmethod
    def _records(raw: dict[str, Any]) -> list[ReturnRecord]:
        records: list[ReturnRecord] = []
        try:
            for returned in _nodes(raw["returns"]):
                grouped: dict[tuple[str, str, str], int] = {}
                for item in _nodes(returned["returnLineItems"]):
                    # UnverifiedReturnLineItem cannot be associated safely with an order line.
                    if "fulfillmentLineItem" not in item:
                        raise ProviderError("This return needs merchant review.")
                    line_id = item["fulfillmentLineItem"]["lineItem"]["id"]
                    marker = _MARKER.search(item.get("customerNote") or "")
                    operation = marker.group(1) if marker else ""
                    reason = marker.group(2) if marker else {
                        "DEFECTIVE": "damaged", "WRONG_ITEM": "wrong_item", "UNWANTED": "changed_mind",
                        "SIZE_TOO_SMALL": "size", "SIZE_TOO_LARGE": "size",
                    }.get(item.get("returnReason"), "other")
                    key = (line_id, operation, reason)
                    grouped[key] = grouped.get(key, 0) + int(item["quantity"])
                for (line_id, operation, reason), quantity in grouped.items():
                    records.append(ReturnRecord(id=returned["id"], number=returned["name"],
                        order_id=raw["id"], line_id=line_id, quantity=quantity, reason=reason,
                        status=returned["status"], created_at=_date(returned["createdAt"]),
                        operation_id=operation, mode="shopify"))
            return records
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ProviderError("Shopify returned incomplete return data.") from None

    async def get_order(self, number: str, customer_ref: str) -> Order | None:
        number = number.strip().lstrip("#")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", number):
            return None
        if not re.fullmatch(r"gid://shopify/Customer/\d+", customer_ref):
            return None
        data = await self._graphql(_SEARCH, {"query": f'name:"#{number}"'})
        try:
            matches = [r for r in _nodes(data["orders"])
                       if r["name"].lstrip("#") == number and self._customer_matches(r, customer_ref)]
            if not matches:
                return None
            if len(matches) != 1:
                raise ProviderError("This order needs merchant review.")
            raw = (await self._graphql(_ORDER, {"id": matches[0]["id"]}))["order"]
            if not self._customer_matches(raw, customer_ref) or raw.get("id") != matches[0]["id"]:
                return None
            line_nodes = _nodes(raw["lineItems"])
            returns = self._records(raw)
            fulfillments = raw["fulfillments"]
            if len(fulfillments) >= 20:
                raise ProviderError("This order has too many shipments and needs merchant review.")
            dates = [_date(f["createdAt"]) for f in fulfillments if f["status"] == "SUCCESS"]
            available = await self._returnable(raw["id"])
            money = raw["totalPriceSet"]["presentmentMoney"]
            return Order(id=raw["id"], number=raw["name"], customer_ref=customer_ref,
                fulfillment_status=raw["displayFulfillmentStatus"],
                # Conservative sample policy: a later shipment never extends an older item's window.
                fulfilled_at=min(dates) if dates else None, currency=money["currencyCode"], total=money["amount"],
                lines=[LineItem(id=line["id"], title=line["title"], quantity=line["quantity"],
                    returnable_quantity=sum(q for _, q in available.get(line["id"], [])),
                    returnable_id=available[line["id"]][0][0] if len(available.get(line["id"], [])) == 1 else None,
                    excluded=bool(line["isGiftCard"])) for line in line_nodes], returns=returns, mode="shopify")
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ProviderError("Shopify returned incomplete order data.") from None

    async def find_return(self, order: Order, operation_id: str) -> ReturnRecord | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", operation_id):
            raise ProviderError("Invalid return operation reference.")
        data = await self._graphql(_RETURNS, {"id": order.id})
        raw = data.get("order")
        if not self._customer_matches(raw, order.customer_ref) or raw.get("id") != order.id:
            raise ProviderError("The order is not available for this customer.")
        matches = [r for r in self._records(raw) if r.operation_id == operation_id]
        if len(matches) > 1:
            raise AmbiguousAction("More than one return matches this reference; merchant review is required.")
        return matches[0] if matches else None

    async def request_return(self, order: Order, line_id: str, quantity: int,
                             reason: Reason, operation_id: str) -> ReturnRecord:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", operation_id):
            raise ProviderError("Invalid return operation reference.")
        if type(quantity) is not int or quantity < 1 or quantity > 20 or reason not in _REASONS:
            raise ProviderError("Invalid return quantity or reason.")
        existing = await self.find_return(order, operation_id)
        if existing:
            if (existing.line_id, existing.quantity, existing.reason) != (line_id, quantity, reason):
                raise AmbiguousAction("The existing return differs from this request; merchant review is required.")
            return existing
        if not any(line.id == line_id and not line.excluded for line in order.lines):
            raise ProviderError("This item is not available for a return request.")
        # Refresh actual fulfillment IDs and quantities immediately before the mutation.
        available = (await self._returnable(order.id)).get(line_id, [])
        if sum(q for _, q in available) < quantity:
            raise ProviderError("This quantity is no longer available to return. Refresh the order.")
        remaining, inputs = quantity, []
        note = f"Reason: {reason.replace('_', ' ')}. [orderfix:{operation_id};reason:{reason}]"
        for fulfillment_id, count in available:
            amount = min(remaining, count)
            if amount:
                inputs.append({"fulfillmentLineItemId": fulfillment_id, "quantity": amount,
                    "returnReason": _REASONS[reason], "customerNote": note})
                remaining -= amount
            if remaining == 0:
                break
        data = await self._graphql(_REQUEST, {"input": {"orderId": order.id, "returnLineItems": inputs}}, mutation=True)
        payload = data.get("returnRequest")
        if not isinstance(payload, dict):
            raise AmbiguousAction("Shopify did not confirm the return. Check its status before trying again.")
        if payload.get("userErrors") and not payload.get("return"):
            raise ProviderError("Shopify declined this return request. Refresh the order or ask the merchant to review it.")
        if not isinstance(payload.get("return"), dict) or not payload["return"].get("id"):
            raise AmbiguousAction("Shopify did not confirm the return. Check its status before trying again.")
        try:
            record = await self.find_return(order, operation_id)
        except ProviderError:
            raise AmbiguousAction("The request was sent, but verification is unavailable. Check its status before trying again.") from None
        if (not record or record.id != payload["return"]["id"] or
                (record.line_id, record.quantity, record.reason) != (line_id, quantity, reason)):
            raise AmbiguousAction("The request was sent, but its details could not be verified. Ask the merchant to review it.")
        return record
