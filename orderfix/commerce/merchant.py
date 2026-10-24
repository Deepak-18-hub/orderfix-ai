"""Opt-in merchant API bridge for a Med Guardian operator.

This is a contract an authorized merchant must implement, not an API claimed to
exist at any public pharmacy. It has no default host, demo orders or payment
fallback. Include its signed protocol in the teammate's privately seeded agent.
The merchant must authenticate customer ownership and implement atomic version
checks and durable idempotency against its actual order system.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError, field_validator
from uagents import Protocol

from .models import PharmacyOrder
from .protocol import (
    LIVE_PROTOCOL_VERSION, MAX_TTL, LivePeerRequest, LivePeerResponse, PeerValidationError,
    response_for, validate_freshness, validate_sender,
)

MERCHANT_OPERATIONS = frozenset({"orders.list", "order.get", "order.eligibility", "order.cancel"})


def _utc_timestamp(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError("eligibility expiry must be a timezone-aware UTC timestamp")
    return timestamp


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Selection(_Payload):
    order_id: str = Field(min_length=1, max_length=200)

    @field_validator("order_id")
    @classmethod
    def bounded_identifier(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", value):
            raise ValueError("order_id must be an opaque merchant identifier")
        return value


class _Cancellation(_Selection):
    expected_order_version: StrictInt = Field(ge=0)
    approval_ref: str = Field(min_length=1, max_length=128)
    refundable_amount_minor: StrictInt = Field(gt=0, le=100000000)
    currency: Literal["INR"]
    policy_version: str = Field(min_length=1, max_length=128)
    eligibility_expires_at: str

    @field_validator("eligibility_expires_at")
    @classmethod
    def utc_expiry(cls, value):
        # A replay after a lost acknowledgement must preserve the original
        # expiry. The merchant resolves its durable business key before expiry
        # checks, then rejects a first-time mutation whose approval has expired.
        _utc_timestamp(value)
        return value

    @field_validator("approval_ref", "policy_version")
    @classmethod
    def opaque_reference(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
            raise ValueError("approval and policy references must be opaque")
        return value


class _Eligibility(_Payload):
    eligible: StrictBool
    order: dict
    policy_text: str = Field(min_length=1, max_length=4000)
    refundable_amount_minor: StrictInt = Field(ge=0, le=100000000)
    currency: Literal["INR"]
    policy_version: str = Field(min_length=1, max_length=128)
    eligibility_expires_at: str

    @field_validator("policy_version")
    @classmethod
    def policy_reference(cls, value):
        return _Cancellation.opaque_reference(value)


class _CancellationResult(_Payload):
    order: dict
    cancellation_ref: str = Field(min_length=1, max_length=200)
    refundable_amount_minor: StrictInt = Field(gt=0, le=100000000)
    currency: Literal["INR"]
    policy_version: str = Field(min_length=1, max_length=128)

    @field_validator("cancellation_ref")
    @classmethod
    def evidence_reference(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", value):
            raise ValueError("cancellation evidence must have an opaque reference")
        return value


class MerchantContractError(ValueError):
    pass


class MerchantAPIAdapter:
    """Call an explicitly configured, authenticated HTTPS merchant contract.

    GET /orders?customer_ref=... -> {orders: [PharmacyOrder, ...]}
    GET /orders/{id}?customer_ref=... -> {order: PharmacyOrder}
    POST /orders/{id}/cancellation-eligibility -> eligibility fields below
    POST /orders/{id}/cancel -> a verified cancellation result

    POST bodies contain the unchanged request payload plus customer_ref,
    case_id, expected_version (commerce case version), and idempotency_key.
    Idempotency-Key is also a header. The merchant must atomically bind that
    key to the customer, operation and full body and replay the exact outcome;
    changed payloads return HTTP 409. Cancellation verifies
    expected_order_version and advances the order version by exactly one.
    Redirects and automatic mutation retries are disabled. An uncertain cancel
    does not authorize a refund.
    """

    def __init__(self, base_url: str, token: str, *, transport=None, timeout: float = 10.0):
        url = urlsplit(base_url)
        if (url.scheme != "https" or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment
                or any(c.isspace() for c in base_url)):
            raise ValueError("an explicit HTTPS merchant base URL without credentials/query is required")
        if not token or any(c.isspace() for c in token):
            raise ValueError("a merchant API bearer token is required")
        if not 0 < timeout <= 60:
            raise ValueError("merchant timeout must be between zero and 60 seconds")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._transport = transport
        self._timeout = timeout

    @staticmethod
    def _order(raw: dict, request: LivePeerRequest, order_id: str | None = None) -> dict:
        order = PharmacyOrder.model_validate(raw)
        if (order.customer_ref != request.customer_ref or order.data_mode != "connected"
                or not getattr(order, "provider_order_id", None)
                or (order_id is not None and order.order_id != order_id)):
            raise MerchantContractError("merchant order does not match the authenticated scope")
        # Return only validated values; no inferred amounts, currency or items.
        return order.model_dump(mode="json")

    def _validate_result(self, body: dict, request: LivePeerRequest, fields) -> dict:
        if not isinstance(body, dict):
            raise MerchantContractError("merchant result must be an object")
        if request.operation == "orders.list":
            if set(body) != {"orders"} or not isinstance(body["orders"], list) or len(body["orders"]) > 20:
                raise MerchantContractError("merchant order list is invalid")
            orders = [self._order(raw, request) for raw in body["orders"]]
            if len({order["order_id"] for order in orders}) != len(orders):
                raise MerchantContractError("merchant order list contains duplicates")
            return {"orders": orders}
        if request.operation == "order.get":
            if set(body) != {"order"}:
                raise MerchantContractError("merchant order result is invalid")
            return {"order": self._order(body["order"], request, fields.order_id)}
        if request.operation == "order.eligibility":
            result = _Eligibility.model_validate(body)
            order = self._order(result.order, request, fields.order_id)
            expires = _utc_timestamp(result.eligibility_expires_at)
            now = datetime.now(timezone.utc)
            if (expires <= now or expires > now + MAX_TTL
                    or result.refundable_amount_minor > order["amount_minor"]
                    or (result.eligible and (order["order_state"] != "accepted" or result.refundable_amount_minor <= 0))):
                raise MerchantContractError("merchant eligibility is expired or inconsistent")
            return {**result.model_dump(), "order": order}
        result = _CancellationResult.model_validate(body)
        order = self._order(result.order, request, fields.order_id)
        if (order["order_state"] != "cancelled"
                or order["version"] != fields.expected_order_version + 1
                or result.refundable_amount_minor != fields.refundable_amount_minor
                or result.currency != fields.currency or result.policy_version != fields.policy_version
                or result.refundable_amount_minor > order["amount_minor"]):
            raise MerchantContractError("merchant cancellation does not match the approved action")
        return {**result.model_dump(), "order": order, "cancelled": True}

    async def handle(self, request: LivePeerRequest) -> LivePeerResponse:
        # Only the signed protocol below is an external entry point. Direct
        # callers must already have verified the coordinator's authority.
        if not isinstance(request, LivePeerRequest):
            raise PeerValidationError("merchant adapter accepts only live v2 requests")
        try:
            validate_freshness(request)
        except PeerValidationError:
            return response_for(request, ok=False, error_code="INVALID_ENVELOPE")
        if request.operation not in MERCHANT_OPERATIONS:
            return response_for(request, ok=False, error_code="UNSUPPORTED_OPERATION")
        try:
            payload_type = _Payload if request.operation == "orders.list" else (
                _Cancellation if request.operation == "order.cancel" else _Selection)
            fields = payload_type.model_validate(request.payload)
        except (ValidationError, ValueError, TypeError):
            return response_for(request, ok=False, error_code="INVALID_PAYLOAD")
        path = "/orders" if request.operation == "orders.list" else "/orders/" + quote(fields.order_id, safe="")
        is_post = request.operation in {"order.eligibility", "order.cancel"}
        if is_post:
            path += "/cancel" if request.operation == "order.cancel" else "/cancellation-eligibility"
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json",
                   "Idempotency-Key": request.idempotency_key,
                   "X-QuickFix-Case-Version": str(request.expected_version)}
        kwargs = {"params": {"customer_ref": request.customer_ref}}
        if is_post:
            kwargs = {"json": {**request.payload, "customer_ref": request.customer_ref,
                               "case_id": request.case_id, "expected_version": request.expected_version,
                               "idempotency_key": request.idempotency_key}}
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout,
                                         follow_redirects=False, trust_env=False) as client:
                response = await client.request("POST" if is_post else "GET", self._base_url + path,
                                                headers=headers, **kwargs)
        except httpx.TransportError:
            code = "CANCELLATION_OUTCOME_UNKNOWN" if request.operation == "order.cancel" else "MERCHANT_UNAVAILABLE"
            return response_for(request, ok=False, error_code=code)
        if response.status_code != 200:
            codes = {401: "MERCHANT_UNAUTHORIZED", 403: "MERCHANT_UNAUTHORIZED", 404: "ORDER_NOT_FOUND",
                     409: "MERCHANT_CONFLICT", 422: "MERCHANT_REJECTED"}
            code = codes.get(response.status_code, "MERCHANT_UNAVAILABLE")
            if request.operation == "order.cancel" and response.status_code not in codes:
                code = "CANCELLATION_OUTCOME_UNKNOWN"
            return response_for(request, ok=False, error_code=code)
        try:
            if len(response.content) > 512_000:
                raise MerchantContractError("merchant response exceeds the size limit")
            result = self._validate_result(response.json(), request, fields)
            return response_for(request, ok=True, payload=result)
        except (ValidationError, ValueError, TypeError, KeyError):
            return response_for(request, ok=False, error_code="INVALID_MERCHANT_RESPONSE")


def make_merchant_protocol(adapter: MerchantAPIAdapter, coordinator_address: str) -> Protocol:
    """Include in Med Guardian using its private, verified agent identity."""
    protocol = Protocol(name="quickfix-merchant-live", version=LIVE_PROTOCOL_VERSION)
    allowed = {coordinator_address} if coordinator_address else set()

    @protocol.on_message(LivePeerRequest, replies=LivePeerResponse, allow_unverified=False)
    async def receive(ctx, sender: str, request: LivePeerRequest):
        try:
            validate_sender(sender, allowed)
            validate_freshness(request)
        except PeerValidationError:
            ctx.logger.warning("Rejected unauthorized or expired merchant request")
            return
        await ctx.send(sender, await adapter.handle(request))

    return protocol
