from __future__ import annotations

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from uagents import Model

from orderfix.commerce.peers import (
    MailService, PharmacyService, make_mail_protocol, make_pharmacy_protocol,
)
from orderfix.commerce.protocol import PeerRequest


def request(operation="orders.list", payload=None, **changes):
    now = datetime.now(timezone.utc)
    values = dict(request_id=str(uuid4()), correlation_id="corr-1", case_id="case-1",
                  customer_ref="demo-alice", operation=operation, idempotency_key=str(uuid4()),
                  created_at=now, expires_at=now + timedelta(minutes=5), expected_version=1,
                  mode="sandbox", payload=payload or {})
    values.update(changes)
    return PeerRequest(**values)


def mail_payload(**changes):
    values = dict(event_id="event-1", recipient_role="customer", recipient="patient@example.invalid",
                  template_version="1", language="en", order={"title": "Synthetic order", "pharmacy_name": "Fictional Pharmacy"},
                  refund={"amount_minor": 34900, "currency": "INR", "status": "pending"}, order_state="cancelled")
    values.update(changes)
    return values


def test_pharmacy_seeds_only_explicitly_and_scopes_customer(tmp_path):
    service = PharmacyService(tmp_path / "pharmacy.db")
    assert service.handle(request()).payload == {"orders": []}
    ids = service.seed_customer("demo-alice")
    orders = service.handle(request()).payload["orders"]
    assert [order["order_id"] for order in orders] == ids
    assert [order["amount_minor"] for order in orders] == [34900, 19900]
    assert all(order["data_mode"] == "simulated" for order in orders)
    assert service.handle(request(customer_ref="demo-bob")).payload == {"orders": []}
    denied = service.handle(request("order.get", {"order_id": ids[0]}, customer_ref="demo-bob"))
    assert denied.error_code == "ORDER_NOT_FOUND" and denied.payload == {}
    service.close()


def test_auto_seed_is_opt_in_and_demo_only(tmp_path):
    service = PharmacyService(tmp_path / "pharmacy.db", auto_seed=True)
    assert service.handle(request(customer_ref="connected-person")).payload["orders"] == []
    first = service.handle(request()).payload["orders"]
    second = service.handle(request()).payload["orders"]
    assert len(first) == 2 and first == second
    service.close()


def test_cancellation_is_durable_idempotent_and_checks_version(tmp_path):
    path = tmp_path / "pharmacy.db"
    service = PharmacyService(path)
    order_id = service.seed_customer("demo-alice")[0]
    original = request("order.cancel", {"order_id": order_id, "expected_order_version": 1, "approval_ref": "approved-1"})
    result = service.handle(original)
    assert result.ok and result.payload["order"]["version"] == 2
    assert result.payload["order"]["order_state"] == "cancelled"
    service.close()
    restarted = PharmacyService(path)
    retry = request("order.cancel", original.payload, idempotency_key=original.idempotency_key)
    repeated = restarted.handle(retry)
    assert repeated.ok and repeated.payload == result.payload
    assert repeated.request_id == retry.request_id != original.request_id
    stale = restarted.handle(request("order.cancel", original.payload))
    assert stale.error_code == "VERSION_CONFLICT"
    changed = restarted.handle(request("order.cancel", {**original.payload, "approval_ref": "different"},
                                       idempotency_key=original.idempotency_key))
    assert changed.error_code == "IDEMPOTENCY_CONFLICT"
    copied = restarted.handle(request("order.cancel", original.payload, customer_ref="demo-bob",
                                      idempotency_key=original.idempotency_key))
    assert copied.error_code == "IDEMPOTENCY_CONFLICT" and copied.payload == {}
    restarted.close()


def test_dispatch_and_racing_cancellations(tmp_path):
    path = tmp_path / "pharmacy.db"
    first = PharmacyService(path)
    order_ids = first.seed_customer("demo-alice")
    denied = first.handle(request("order.cancel", {"order_id": order_ids[1], "expected_order_version": 1, "approval_ref": "a"}))
    assert denied.error_code == "NOT_ELIGIBLE"
    assert not first.handle(request("order.eligibility", {"order_id": order_ids[1]})).payload["eligible"]
    second = PharmacyService(path)
    cancel = {"order_id": order_ids[0], "expected_order_version": 1, "approval_ref": "a"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service.handle, request("order.cancel", cancel)) for service in (first, second)]
        results = [future.result() for future in futures]
    assert sum(result.ok for result in results) == 1
    assert {result.error_code for result in results} == {None, "VERSION_CONFLICT"}
    first.close()
    second.close()


def test_rejects_unknown_fields_bad_approval_and_expired_requests(tmp_path):
    service = PharmacyService(tmp_path / "pharmacy.db", auto_seed=True)
    assert service.handle(request(payload={"admin": True})).error_code == "INVALID_PAYLOAD"
    assert service.handle(request("order.cancel", {"order_id": "a", "expected_order_version": 1, "approval_ref": ""})).error_code == "INVALID_PAYLOAD"
    now = datetime.now(timezone.utc)
    expired = request(created_at=now - timedelta(minutes=6), expires_at=now - timedelta(minutes=1))
    assert service.handle(expired).error_code == "INVALID_ENVELOPE"
    assert service.handle(request("mail.send", mail_payload())).error_code == "UNSUPPORTED_OPERATION"
    service.close()


@pytest.mark.parametrize("language,marker", [("en", "not sent"), ("hi", "भेजा नहीं"), ("kn", "ಕಳುಹಿಸಲಾಗಿಲ್ಲ")])
def test_mail_captures_persistently_without_claiming_delivery(tmp_path, language, marker):
    path = tmp_path / "mail.db"
    service = MailService(path)
    pending = request("mail.send", mail_payload(language=language))
    result = service.handle(pending)
    assert result.ok and result.payload["state"] == "captured"
    assert result.payload["provider_message_id"] is None
    assert result.payload["evidence_mode"] == "captured"
    service.close()
    restarted = MailService(path)
    assert restarted.handle(pending).payload == result.payload
    assert restarted.handle(request("mail.send", pending.payload)).payload == result.payload
    status = restarted.handle(request("mail.status", {"notification_id": result.payload["notification_id"]}))
    assert status.payload == result.payload
    other = restarted.handle(request("mail.status", {"notification_id": result.payload["notification_id"]}, customer_ref="demo-bob"))
    assert other.error_code == "NOTIFICATION_NOT_FOUND"
    with sqlite3.connect(path) as db:
        captures = db.execute("SELECT subject,body FROM peer_mail_captures").fetchall()
    assert len(captures) == 1 and marker in captures[0][1]
    assert "pending" in captures[0][1]
    restarted.close()


def test_mail_rejects_event_changes_and_extra_authority(tmp_path):
    service = MailService(tmp_path / "mail.db")
    original = request("mail.send", mail_payload())
    assert service.handle(original).ok
    changed = service.handle(request("mail.send", mail_payload(recipient="another@example.invalid")))
    assert changed.error_code == "EVENT_CONFLICT"
    copied = service.handle(request("mail.send", mail_payload(), customer_ref="demo-bob"))
    assert copied.error_code == "EVENT_CONFLICT"
    broad = service.handle(request("mail.send", mail_payload(smtp_server="example.com")))
    assert broad.error_code == "INVALID_PAYLOAD"
    service.close()


@pytest.mark.parametrize("factory,service_class,operation,payload", [
    (make_pharmacy_protocol, PharmacyService, "orders.list", {}),
    (make_mail_protocol, MailService, "mail.send", mail_payload()),
])
def test_registered_peer_handlers_enforce_transport_sender_and_send_reply(tmp_path, factory, service_class, operation, payload):
    service = service_class(tmp_path / "peer.db")
    protocol = factory(service, "quickfix-agent")
    assert not protocol.unsigned_message_handlers
    handler = protocol.signed_message_handlers[Model.build_schema_digest(PeerRequest)]
    sent = []

    async def send(address, message):
        sent.append((address, message))

    ctx = SimpleNamespace(send=send, logger=SimpleNamespace(warning=lambda *_: None))
    message = request(operation, payload)

    async def exercise():
        await handler(ctx, "attacker", message)
        assert not sent
        await handler(ctx, "quickfix-agent", message)

    asyncio.run(exercise())
    assert len(sent) == 1 and sent[0][0] == "quickfix-agent"
    assert sent[0][1].request_id == message.request_id
    assert sent[0][1].ok
    service.close()
