import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic.v1 import ValidationError
from uagents import Model

from orderfix.commerce.protocol import (
    LivePeerRequest, LivePeerResponse, PeerRequest, PeerResponse, PeerValidationError,
    make_peer_protocol, parse_peer_request, response_for, validate_freshness, validate_response,
)


def live_request(**changes):
    now = datetime.now(timezone.utc)
    fields = dict(request_id=str(uuid4()), correlation_id="corr-1", case_id="case-1",
                  customer_ref="customer-1", operation="order.get", idempotency_key="get-1",
                  created_at=now, expires_at=now + timedelta(minutes=5), expected_version=1,
                  payload={"order_id": "merchant-1"})
    fields.update(changes)
    return LivePeerRequest(**fields)


def test_sandbox_schemas_unchanged_and_live_digests_different():
    assert Model.build_schema_digest(PeerRequest) == "model:bc944adbc4c743aacf0ef6b1912a54a27c10431450e350a8562756a0b04f28f4"
    assert Model.build_schema_digest(PeerResponse) == "model:35d90da0110f8f11f4ca6401ef3e3633d493e1fbf75b77f759fc8c0c69630a1c"
    assert Model.build_schema_digest(LivePeerRequest) != Model.build_schema_digest(PeerRequest)
    assert Model.build_schema_digest(LivePeerResponse) != Model.build_schema_digest(PeerResponse)


def test_live_roundtrip_and_response_preserves_exact_envelope():
    pending = live_request()
    assert isinstance(parse_peer_request(pending.dict()), LivePeerRequest)
    result = response_for(pending, ok=True, payload={"orders": []})
    assert isinstance(result, LivePeerResponse)
    assert result.protocol_version == "2.0" and result.mode == "live"
    validate_response(result, pending, "merchant-agent", {"merchant-agent"})
    with pytest.raises(ValidationError):
        PeerResponse.parse_raw(result.json())


@pytest.mark.parametrize("version,mode", [("1.0", "live"), ("2.0", "sandbox"), ("3.0", "live")])
def test_mode_version_cannot_be_mixed(version, mode):
    raw = live_request().dict()
    raw.update(protocol_version=version, mode=mode)
    with pytest.raises((ValidationError, PeerValidationError)):
        parse_peer_request(raw)
    with pytest.raises(PeerValidationError):
        validate_freshness(LivePeerRequest.construct(**raw))


def test_existing_sandbox_records_parse_without_upgrade():
    raw = live_request().dict()
    raw.update(protocol_version="1.0", mode="sandbox")
    assert type(parse_peer_request(raw)) is PeerRequest
    raw.pop("protocol_version")
    raw.pop("mode")
    assert type(parse_peer_request(raw)) is PeerRequest


def test_signed_receiver_enforces_allowlist_and_expiry_for_live_responses():
    calls = []
    pending = live_request()
    protocol = make_peer_protocol(lambda ctx, sender, msg: calls.append(msg), {"merchant-agent"})
    assert not protocol.unsigned_message_handlers
    handler = protocol.signed_message_handlers[Model.build_schema_digest(LivePeerResponse)]
    ctx = SimpleNamespace(logger=SimpleNamespace(warning=lambda *_: None))
    now = datetime.now(timezone.utc)
    expired = live_request(created_at=now - timedelta(minutes=6), expires_at=now - timedelta(minutes=1))

    async def run():
        await handler(ctx, "attacker", response_for(pending, ok=True))
        await handler(ctx, "merchant-agent", response_for(expired, ok=True))
        assert not calls
        await handler(ctx, "merchant-agent", response_for(pending, ok=True))

    asyncio.run(run())
    assert len(calls) == 1


def test_live_inherits_request_and_response_validation():
    with pytest.raises(ValidationError):
        live_request(expected_version=True)
    with pytest.raises(ValidationError):
        live_request(payload={"x": float("nan")})
    with pytest.raises(ValidationError):
        response_for(live_request(), ok="true")
