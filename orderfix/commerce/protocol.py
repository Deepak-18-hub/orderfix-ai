"""Versioned, signed uAgents messages for the sandbox commerce peers.

The framework verifies message signatures. These handlers additionally check the
actual transport sender; a customer reference in a payload is never identity.
"""
from __future__ import annotations

import inspect
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Literal
from uuid import UUID

from pydantic.v1 import Field, root_validator, validator
from uagents import Model, Protocol

PROTOCOL_VERSION = "1.0"
MAX_TTL = timedelta(minutes=15)
CLOCK_SKEW = timedelta(seconds=30)
OPERATIONS = frozenset({"orders.list", "order.eligibility", "order.cancel", "order.get", "mail.send", "mail.status"})
Operation = Literal["orders.list", "order.eligibility", "order.cancel", "order.get", "mail.send", "mail.status"]
ENVELOPE_FIELDS = (
    "protocol_version", "request_id", "correlation_id", "case_id", "customer_ref",
    "operation", "idempotency_key", "created_at", "expires_at", "expected_version", "mode",
)
_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class PeerValidationError(ValueError):
    """An envelope, sender or pending-request binding is invalid."""


class PeerEnvelope(Model):
    protocol_version: Literal["1.0"] = PROTOCOL_VERSION
    request_id: str
    correlation_id: str
    case_id: str
    customer_ref: str
    operation: Operation
    idempotency_key: str
    created_at: datetime
    expires_at: datetime
    expected_version: int = Field(ge=0, le=2**31 - 1)
    mode: Literal["sandbox"] = "sandbox"

    class Config:
        extra = "forbid"
        allow_mutation = False

    @validator("request_id")
    def canonical_uuid(cls, value: str) -> str:
        try:
            if str(UUID(value)) != value:
                raise ValueError("request_id must be a canonical UUID")
        except (ValueError, AttributeError) as exc:
            raise ValueError("request_id must be a canonical UUID") from exc
        return value

    @validator("correlation_id", "case_id", "customer_ref", "idempotency_key", pre=True)
    def opaque_reference(cls, value: str) -> str:
        if not isinstance(value, str) or not _OPAQUE.fullmatch(value):
            raise ValueError("references must be opaque identifiers, not personal information")
        return value

    @validator("expected_version", pre=True)
    def strict_version(cls, value: int) -> int:
        if type(value) is not int:
            raise ValueError("expected_version must be an integer")
        return value

    @validator("created_at", "expires_at")
    def utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("timestamps must be timezone-aware UTC")
        return value.astimezone(timezone.utc)

    @root_validator(skip_on_failure=True)
    def bounded_lifetime(cls, values: dict) -> dict:
        lifetime = values["expires_at"] - values["created_at"]
        if lifetime <= timedelta(0) or lifetime > MAX_TTL:
            raise ValueError("envelope lifetime must be positive and at most 15 minutes")
        return values


class PeerRequest(PeerEnvelope):
    payload: dict[str, Any] = Field(default_factory=dict)

    @validator("payload")
    def bounded_json_payload(cls, value: dict) -> dict:
        try:
            encoded = json.dumps(value, allow_nan=False, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("payload must contain JSON values") from exc
        if len(encoded.encode("utf-8")) > 32_768:
            raise ValueError("payload exceeds 32 KiB")
        return value


class PeerResponse(PeerRequest):
    ok: bool
    error_code: str | None = None

    @validator("ok", pre=True)
    def strict_result(cls, value: bool) -> bool:
        if type(value) is not bool:
            raise ValueError("ok must be a boolean")
        return value

    @root_validator(skip_on_failure=True)
    def consistent_result(cls, values: dict) -> dict:
        error = values.get("error_code")
        if values["ok"] and error is not None:
            raise ValueError("successful responses cannot contain error_code")
        if not values["ok"] and (not isinstance(error, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", error)):
            raise ValueError("failed responses require a bounded error code")
        return values


def response_for(request: PeerRequest, *, ok: bool, payload: dict | None = None,
                 error_code: str | None = None) -> PeerResponse:
    return PeerResponse(**{key: getattr(request, key) for key in ENVELOPE_FIELDS},
                        ok=ok, payload=payload or {}, error_code=error_code)


def validate_freshness(message: PeerEnvelope, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise PeerValidationError("verification time must be UTC")
    if message.mode != "sandbox" or message.protocol_version != PROTOCOL_VERSION:
        raise PeerValidationError("unsupported mode or version")
    if message.created_at > now + CLOCK_SKEW:
        raise PeerValidationError("message creation time is in the future")
    if message.expires_at <= now:
        raise PeerValidationError("message expired")


def validate_sender(actual_sender: str, allowed_senders: Iterable[str]) -> None:
    # Never trust an address copied into request.payload. Empty configuration is
    # deliberately closed, and unsigned handlers are never registered below.
    if actual_sender not in frozenset(allowed_senders):
        raise PeerValidationError("peer sender is not allowed")


def validate_response(response: PeerResponse, request: PeerRequest, actual_sender: str,
                      allowed_senders: Iterable[str], now: datetime | None = None) -> None:
    validate_sender(actual_sender, allowed_senders)
    validate_freshness(response, now)
    for field in ENVELOPE_FIELDS:
        if getattr(response, field) != getattr(request, field):
            raise PeerValidationError(f"response does not match pending request: {field}")


def make_peer_protocol(on_response: Callable, allowed_senders: Iterable[str]) -> Protocol:
    """Coordinator receiver; callback must bind response to its pending request.

    Callback signature is ``on_response(ctx, actual_sender, response)``. It can
    be synchronous or asynchronous and must call validate_response with the
    stored request before changing commerce state.
    """
    allowed = frozenset(allowed_senders)
    protocol = Protocol(name="quickfix-commerce-responses", version=PROTOCOL_VERSION)

    @protocol.on_message(PeerResponse, allow_unverified=False)
    async def receive(ctx, sender: str, message: PeerResponse):
        try:
            validate_sender(sender, allowed)
            validate_freshness(message)
        except PeerValidationError:
            ctx.logger.warning("Rejected commerce peer response")
            return
        result = on_response(ctx, sender, message)
        if inspect.isawaitable(result):
            await result

    return protocol
