"""Persistent, explicitly simulated pharmacy and captured-mail uAgents peers."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic.v1 import BaseModel, Field, StrictInt, ValidationError, validator
from uagents import Protocol

from .protocol import (
    PROTOCOL_VERSION, PeerRequest, PeerResponse, PeerValidationError,
    response_for, validate_freshness, validate_sender,
)


class _Payload(BaseModel):
    class Config:
        extra = "forbid"


class _Item(_Payload):
    name: str = Field(min_length=1, max_length=160)
    strength: str = Field(min_length=1, max_length=80)
    form: str = Field(min_length=1, max_length=80)
    quantity: StrictInt

    @validator("quantity")
    def positive_quantity(cls, value):
        if not 1 <= value <= 1000:
            raise ValueError("quantity outside supported range")
        return value


class _Order(_Payload):
    order_id: str = Field(min_length=1, max_length=200)
    customer_ref: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    pharmacy_ref: str = Field(min_length=1, max_length=128)
    pharmacy_name: str = Field(min_length=1, max_length=160)
    merchant_ref: str = Field(min_length=1, max_length=128)
    payment_id: str = Field(min_length=1, max_length=160)
    amount_minor: StrictInt
    currency: Literal["INR"] = "INR"
    order_state: Literal["accepted", "dispatched", "cancelled"]
    version: StrictInt
    customer_email: str = Field(min_length=3, max_length=254)
    pharmacy_email: str = Field(min_length=3, max_length=254)
    items: list[_Item] = Field(min_items=1, max_items=20)
    data_mode: Literal["simulated", "connected"] = "simulated"

    @validator("amount_minor", "version")
    def bounded_numbers(cls, value, field):
        minimum = 1 if field.name == "amount_minor" else 0
        if not minimum <= value <= 2**31 - 1:
            raise ValueError("numeric value outside supported range")
        return value


class _OrderId(_Payload):
    order_id: str = Field(min_length=1, max_length=200)


class _Cancel(_OrderId):
    expected_order_version: StrictInt
    approval_ref: str = Field(min_length=1, max_length=128)

    @validator("expected_order_version")
    def valid_version(cls, value):
        if value < 0:
            raise ValueError("version must be nonnegative")
        return value

    @validator("approval_ref")
    def opaque_approval(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
            raise ValueError("approval must be an opaque reference")
        return value


class _MailOrder(_Payload):
    title: str = Field(min_length=1, max_length=200)
    pharmacy_name: str = Field(min_length=1, max_length=160)


class _Refund(_Payload):
    amount_minor: StrictInt
    currency: Literal["INR"]
    status: str = Field(min_length=1, max_length=64)
    refund_id: str | None = Field(default=None, max_length=160)

    @validator("amount_minor")
    def valid_amount(cls, value):
        if not 0 <= value <= 2**31 - 1:
            raise ValueError("invalid refund amount")
        return value

    @validator("status")
    def machine_status(cls, value):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value):
            raise ValueError("status must be a machine value")
        return value


class _MailSend(_Payload):
    event_id: str = Field(min_length=1, max_length=128)
    recipient_role: Literal["customer", "pharmacy"]
    recipient: str = Field(min_length=3, max_length=254)
    template_version: Literal["1"]
    language: Literal["en", "hi", "kn"]
    order: _MailOrder
    refund: _Refund
    order_state: Literal["accepted", "dispatched", "cancelled"]

    @validator("recipient")
    def email_address(cls, value):
        if not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+", value):
            raise ValueError("invalid recipient")
        return value

    @validator("event_id")
    def opaque_event(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
            raise ValueError("event must be an opaque reference")
        return value


class _MailStatus(_Payload):
    notification_id: str = Field(min_length=1, max_length=128)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _fingerprint(request: PeerRequest) -> str:
    # A transport retry may use a fresh UUID/deadline. Its authority, action,
    # business version and payload must remain identical to the original.
    values = {key: getattr(request, key) for key in (
        "protocol_version", "case_id", "customer_ref", "operation", "expected_version", "mode", "payload"
    )}
    return hashlib.sha256(_json(values).encode("utf-8")).hexdigest()


class _PersistentService:
    operations: frozenset[str] = frozenset()
    service_name = "abstract"

    def __init__(self, path: str | Path):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), timeout=10, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout=10000")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS peer_results (
            service TEXT NOT NULL, idempotency_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
            ok INTEGER NOT NULL, payload TEXT NOT NULL, error_code TEXT,
            PRIMARY KEY(service, idempotency_key))""")

    def close(self):
        self._db.close()

    def handle(self, request: PeerRequest) -> PeerResponse:
        """Core service entry point, reached through signed allowlisted handlers.

        Unit tests can exercise persistence here. It is not a network endpoint;
        the protocol wrapper below supplies transport authentication.
        """
        try:
            validate_freshness(request)
        except PeerValidationError:
            return response_for(request, ok=False, error_code="INVALID_ENVELOPE")
        if request.operation not in self.operations:
            return response_for(request, ok=False, error_code="UNSUPPORTED_OPERATION")
        digest = _fingerprint(request)
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                prior = self._db.execute(
                    "SELECT * FROM peer_results WHERE service=? AND idempotency_key=?",
                    (self.service_name, request.idempotency_key),
                ).fetchone()
                if prior:
                    self._db.execute("COMMIT")
                    if prior["fingerprint"] != digest:
                        return response_for(request, ok=False, error_code="IDEMPOTENCY_CONFLICT")
                    return response_for(request, ok=bool(prior["ok"]), payload=json.loads(prior["payload"]),
                                        error_code=prior["error_code"])
                try:
                    ok, payload, error = self._dispatch(request)
                except (ValidationError, ValueError, TypeError):
                    ok, payload, error = False, {}, "INVALID_PAYLOAD"
                response = response_for(request, ok=ok, payload=payload, error_code=error)
                self._db.execute(
                    "INSERT INTO peer_results VALUES (?,?,?,?,?,?)",
                    (self.service_name, request.idempotency_key, digest, int(ok), _json(payload), error),
                )
                self._db.execute("COMMIT")
                return response
            except Exception:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                return response_for(request, ok=False, error_code="STORAGE_ERROR")

    def _dispatch(self, request: PeerRequest) -> tuple[bool, dict, str | None]:
        raise NotImplementedError


class PharmacyService(_PersistentService):
    """Fictional pharmacy order authority. It never contacts a real pharmacy."""
    service_name = "pharmacy"
    operations = frozenset({"orders.list", "order.get", "order.eligibility", "order.cancel"})

    def __init__(self, path: str | Path, auto_seed: bool = False):
        super().__init__(path)
        self.auto_seed = auto_seed
        self._db.execute("""CREATE TABLE IF NOT EXISTS peer_pharmacy_orders (
            order_id TEXT PRIMARY KEY, customer_ref TEXT NOT NULL, document TEXT NOT NULL)""")
        self._db.execute("""CREATE TABLE IF NOT EXISTS peer_pharmacy_customers (
            customer_ref TEXT PRIMARY KEY)""")

    def seed_order(self, order: dict) -> None:
        order = _Order(**order).dict()
        if order["data_mode"] != "simulated":
            raise ValueError("pharmacy simulator accepts only explicitly simulated orders")
        with self._lock:
            self._db.execute("INSERT INTO peer_pharmacy_orders VALUES (?,?,?)",
                             (order["order_id"], order["customer_ref"], _json(order)))

    def seed_customer(self, customer_ref: str, payment_ids: list[str] | None = None) -> list[str]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", customer_ref):
            raise ValueError("customer_ref must be opaque")
        if payment_ids is not None and (len(payment_ids) != 2 or any(not p for p in payment_ids)):
            raise ValueError("provide exactly two payment IDs")
        with self._lock:
            own_transaction = not self._db.in_transaction
            if own_transaction:
                self._db.execute("BEGIN IMMEDIATE")
            try:
                result = self._seed_customer(customer_ref, payment_ids)
                if own_transaction:
                    self._db.execute("COMMIT")
                return result
            except Exception:
                if own_transaction:
                    self._db.execute("ROLLBACK")
                raise

    def _seed_customer(self, customer_ref: str, payment_ids: list[str] | None = None) -> list[str]:
        order_ids = [f"demo-{customer_ref}-{i}" for i in (1, 2)]
        if self._db.execute("SELECT 1 FROM peer_pharmacy_customers WHERE customer_ref=?", (customer_ref,)).fetchone():
            return order_ids
        subject_hash = hashlib.sha256(customer_ref.encode("utf-8")).hexdigest()[:16]
        payment_ids = payment_ids or [f"pay_sim_{subject_hash}{i}" for i in (1, 2)]
        for index, amount in enumerate((34900, 19900), 1):
            self.seed_order({
                "order_id": order_ids[index - 1], "customer_ref": customer_ref,
                "title": f"Synthetic medicine order {index}", "pharmacy_ref": "demo-pharmacy",
                "pharmacy_name": "Demo Willow Pharmacy (fictional)", "merchant_ref": "demo-pharmacy",
                "payment_id": payment_ids[index - 1], "amount_minor": amount, "currency": "INR",
                "order_state": "accepted" if index == 1 else "dispatched", "version": 1,
                "customer_email": "patient@example.invalid", "pharmacy_email": "pharmacy@example.invalid",
                "items": [{"name": "Demo medicine — not for use", "strength": "demo strength",
                           "form": "synthetic tablet", "quantity": 10}], "data_mode": "simulated",
            })
        self._db.execute("INSERT INTO peer_pharmacy_customers VALUES (?)", (customer_ref,))
        return order_ids

    def _dispatch(self, request: PeerRequest):
        if request.operation == "orders.list":
            _Payload(**request.payload)
            if self.auto_seed and request.customer_ref.startswith("demo-"):
                self._seed_customer(request.customer_ref)
            rows = self._db.execute("SELECT document FROM peer_pharmacy_orders WHERE customer_ref=? ORDER BY order_id",
                                    (request.customer_ref,)).fetchall()
            return True, {"orders": [json.loads(row[0]) for row in rows]}, None

        fields = (_Cancel if request.operation == "order.cancel" else _OrderId)(**request.payload)
        row = self._db.execute("SELECT document FROM peer_pharmacy_orders WHERE order_id=? AND customer_ref=?",
                               (fields.order_id, request.customer_ref)).fetchone()
        if not row:
            return False, {}, "ORDER_NOT_FOUND"
        order = json.loads(row[0])
        if request.operation == "order.get":
            return True, {"order": order}, None
        if request.operation == "order.eligibility":
            return True, {
                "eligible": order["order_state"] == "accepted", "order": order,
                "policy_text": "Simulation policy: cancellation is allowed before dispatch. Dispatched medicine orders need pharmacy review; no automated return or restocking.",
            }, None
        if fields.expected_order_version != order["version"]:
            return False, {"order": order}, "VERSION_CONFLICT"
        if order["order_state"] != "accepted":
            return False, {"order": order}, "NOT_ELIGIBLE"
        order["order_state"] = "cancelled"
        order["version"] += 1
        self._db.execute("UPDATE peer_pharmacy_orders SET document=? WHERE order_id=? AND customer_ref=?",
                         (_json(order), order["order_id"], request.customer_ref))
        return True, {"order": order, "cancelled": True}, None


def _render_mail(payload: _MailSend) -> tuple[str, str]:
    amount = f"{payload.refund.amount_minor // 100}.{payload.refund.amount_minor % 100:02d} {payload.refund.currency}"
    # Status tokens are quoted as recorded facts; templates never infer that a
    # request or pending refund means settlement. No therapeutic advice appears.
    labels = {
        "en": ("OrderFix sandbox order update", "CAPTURED EMAIL — stored locally; not sent.",
               "Order", "Pharmacy", "Order status", "Refund status", "Refund amount", "Refund reference"),
        "hi": ("OrderFix सैंडबॉक्स ऑर्डर अपडेट", "ईमेल स्थानीय रूप से सहेजा गया है; भेजा नहीं गया है।",
               "ऑर्डर", "फ़ार्मेसी", "ऑर्डर की स्थिति", "रिफंड की स्थिति", "रिफंड राशि", "रिफंड संदर्भ"),
        "kn": ("OrderFix ಸ್ಯಾಂಡ್‌ಬಾಕ್ಸ್ ಆರ್ಡರ್ ಮಾಹಿತಿ", "ಈ ಇಮೇಲ್ ಸ್ಥಳೀಯವಾಗಿ ಉಳಿಸಲಾಗಿದೆ; ಕಳುಹಿಸಲಾಗಿಲ್ಲ.",
               "ಆರ್ಡರ್", "ಔಷಧಾಲಯ", "ಆರ್ಡರ್ ಸ್ಥಿತಿ", "ಮರುಪಾವತಿ ಸ್ಥಿತಿ", "ಮರುಪಾವತಿ ಮೊತ್ತ", "ಮರುಪಾವತಿ ಉಲ್ಲೇಖ"),
    }[payload.language]
    values = [payload.order.title, payload.order.pharmacy_name, payload.order_state,
              payload.refund.status, amount, payload.refund.refund_id or "—"]
    body = labels[1] + "\n\n" + "\n".join(f"{label}: {value}" for label, value in zip(labels[2:], values))
    return labels[0], body


class MailService(_PersistentService):
    """Captures notification content durably. No SMTP/network sender exists."""
    service_name = "mail"
    operations = frozenset({"mail.send", "mail.status"})

    def __init__(self, path: str | Path):
        super().__init__(path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS peer_mail_captures (
            notification_id TEXT PRIMARY KEY, customer_ref TEXT NOT NULL, event_id TEXT NOT NULL,
            recipient_role TEXT NOT NULL, payload TEXT NOT NULL, subject TEXT NOT NULL,
            body TEXT NOT NULL, UNIQUE(event_id, recipient_role))""")

    @staticmethod
    def _receipt(notification_id: str) -> dict:
        return {"notification_id": notification_id, "state": "captured", "provider_message_id": None,
                "evidence_mode": "captured"}

    def _dispatch(self, request: PeerRequest):
        if request.operation == "mail.status":
            fields = _MailStatus(**request.payload)
            row = self._db.execute("SELECT notification_id FROM peer_mail_captures WHERE notification_id=? AND customer_ref=?",
                                   (fields.notification_id, request.customer_ref)).fetchone()
            return (True, self._receipt(row[0]), None) if row else (False, {}, "NOTIFICATION_NOT_FOUND")
        fields = _MailSend(**request.payload)
        canonical = _json(fields.dict())
        existing = self._db.execute("SELECT * FROM peer_mail_captures WHERE event_id=? AND recipient_role=?",
                                    (fields.event_id, fields.recipient_role)).fetchone()
        if existing:
            if existing["customer_ref"] != request.customer_ref or existing["payload"] != canonical:
                return False, {}, "EVENT_CONFLICT"
            return True, self._receipt(existing["notification_id"]), None
        notification_id = f"captured_{uuid4().hex}"
        subject, body = _render_mail(fields)
        self._db.execute("INSERT INTO peer_mail_captures VALUES (?,?,?,?,?,?,?)",
                         (notification_id, request.customer_ref, fields.event_id, fields.recipient_role,
                          canonical, subject, body))
        return True, self._receipt(notification_id), None


def _make_request_protocol(service: _PersistentService, allowed_sender: str) -> Protocol:
    protocol = Protocol(name=f"quickfix-{service.service_name}-sandbox", version=PROTOCOL_VERSION)

    @protocol.on_message(PeerRequest, replies=PeerResponse, allow_unverified=False)
    async def receive(ctx, sender: str, request: PeerRequest):
        try:
            validate_sender(sender, {allowed_sender} if allowed_sender else set())
        except PeerValidationError:
            ctx.logger.warning("Rejected unauthorized commerce peer request")
            return
        # The framework exposes only verified sender addresses to this handler.
        # Each response is sent through the actual uAgents transport, not a
        # fabricated in-process orchestration result.
        await ctx.send(sender, service.handle(request))

    return protocol


def make_pharmacy_protocol(service: PharmacyService, allowed_sender: str) -> Protocol:
    return _make_request_protocol(service, allowed_sender)


def make_mail_protocol(service: MailService, allowed_sender: str) -> Protocol:
    return _make_request_protocol(service, allowed_sender)
