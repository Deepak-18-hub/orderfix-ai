from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic.v1 import ValidationError
from uagents import Model

from orderfix.commerce.protocol import (
    ENVELOPE_FIELDS, PeerRequest, PeerResponse, PeerValidationError,
    make_peer_protocol, response_for, validate_freshness, validate_response,
)


def request(**changes):
    now = datetime.now(timezone.utc)
    fields = dict(request_id=str(uuid4()), correlation_id="corr-1", case_id="case-1",
                  customer_ref="demo-patient-1", operation="order.get", idempotency_key="get-1",
                  created_at=now, expires_at=now + timedelta(minutes=5), expected_version=1,
                  mode="sandbox", payload={"order_id": "demo-order-1"})
    fields.update(changes)
    return PeerRequest(**fields)


@pytest.mark.parametrize("changes", [
    {"mode": "live"}, {"protocol_version": "2.0"}, {"operation": "payment.charge"},
    {"request_id": "not-a-uuid"}, {"customer_ref": "patient@example.com"},
    {"expected_version": -1}, {"expected_version": True}, {"expected_version": "1"},
    {"created_at": datetime.now()}, {"payload": {"x": float("nan")}},
    {"payload": {"x": "a" * 32_768}}, {"sender": "claimed-agent"},
])
def test_rejects_invalid_or_overbroad_envelopes(changes):
    with pytest.raises(ValidationError):
        request(**changes)


def test_rejects_non_utc_and_long_lived_messages():
    local = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    with pytest.raises(ValidationError):
        request(created_at=local, expires_at=local + timedelta(minutes=5))
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        request(created_at=now, expires_at=now + timedelta(minutes=16))


def test_freshness_rejects_expired_and_future():
    now = datetime.now(timezone.utc)
    for created in (now - timedelta(minutes=6), now + timedelta(minutes=1)):
        message = request(created_at=created, expires_at=created + timedelta(minutes=5))
        with pytest.raises(PeerValidationError):
            validate_freshness(message, now)


def test_successful_response_roundtrips_and_copies_envelope():
    pending = request()
    result = response_for(pending, ok=True, payload={"order": {"version": 2}})
    parsed = PeerResponse.parse_raw(result.json())
    assert all(getattr(parsed, key) == getattr(pending, key) for key in ENVELOPE_FIELDS)
    validate_response(parsed, pending, "pharmacy-agent", {"pharmacy-agent"})
    assert not parsed.error_code


@pytest.mark.parametrize("field,value", [
    ("request_id", str(uuid4())), ("correlation_id", "different"), ("case_id", "different"),
    ("customer_ref", "other-patient"), ("operation", "order.cancel"),
    ("idempotency_key", "different"), ("expected_version", 2),
])
def test_response_must_match_every_pending_identifier(field, value):
    pending = request()
    forged = response_for(pending, ok=True).dict()
    forged[field] = value
    with pytest.raises(PeerValidationError):
        validate_response(PeerResponse(**forged), pending, "pharmacy-agent", {"pharmacy-agent"})


def test_response_deadlines_and_actual_sender_are_bound():
    pending = request()
    response = response_for(pending, ok=True)
    with pytest.raises(PeerValidationError):
        validate_response(response, pending, "attacker", {"pharmacy-agent"})
    with pytest.raises(PeerValidationError):
        validate_response(response, pending, "pharmacy-agent", set())
    forged = response.dict()
    forged["expires_at"] += timedelta(seconds=1)
    with pytest.raises(PeerValidationError):
        validate_response(PeerResponse(**forged), pending, "pharmacy-agent", {"pharmacy-agent"})


def test_response_error_contract():
    pending = request()
    with pytest.raises(ValidationError):
        response_for(pending, ok=True, error_code="ERROR")
    with pytest.raises(ValidationError):
        response_for(pending, ok=False)


def test_coordinator_handler_accepts_only_signed_allowlisted_fresh_responses():
    calls = []
    pending = request()

    async def callback(ctx, sender, response):
        validate_response(response, pending, sender, {"pharmacy-agent"})
        calls.append(response.request_id)

    protocol = make_peer_protocol(callback, {"pharmacy-agent"})
    assert not protocol.unsigned_message_handlers
    handler = protocol.signed_message_handlers[Model.build_schema_digest(PeerResponse)]
    ctx = SimpleNamespace(logger=SimpleNamespace(warning=lambda *_: None))

    async def exercise():
        await handler(ctx, "attacker", response_for(pending, ok=True))
        assert not calls
        await handler(ctx, "pharmacy-agent", response_for(pending, ok=True))

    asyncio.run(exercise())
    assert calls == [pending.request_id]
