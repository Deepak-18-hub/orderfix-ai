"""Test-only payment adapters. No card, prescription, or customer contact data.

Callers must bind the payment to an authenticated customer and merchant before
using this module, and persist their dispatch journal before create_refund().
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
from typing import Literal
from uuid import uuid4

import httpx


class PaymentUnavailable(Exception):
    """A read could not establish the provider's state; no refund was submitted."""


class PaymentRejected(Exception):
    """Invalid request or an explicit rejection; do not blindly retry."""


class PaymentAmbiguous(Exception):
    """A refund may exist. Reconcile the same operation before any further action."""


@dataclass(frozen=True)
class PaymentSnapshot:
    id: str
    merchant_ref: str
    status: str
    amount: int
    currency: str
    amount_refunded: int


@dataclass(frozen=True)
class RefundSnapshot:
    id: str
    payment_id: str
    amount: int
    currency: str
    status: Literal["pending", "processed", "failed"]
    operation_id: str


_PAYMENT_STATES = {"created", "authorized", "captured", "refunded", "failed"}
_REFUND_STATES = {"pending", "processed", "failed"}


def _identifier(value: str, prefix: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(prefix + r"_[A-Za-z0-9_-]{1,100}", value):
        raise PaymentRejected("Invalid payment or refund identifier.")


def _operation(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,128}", value):
        raise PaymentRejected("Operation ID must contain 10–128 letters, digits, hyphens or underscores.")


def _money(amount: int, currency: str) -> None:
    if type(amount) is not int or amount <= 0 or amount > 9_000_000_000_000_000:
        raise PaymentRejected("Amount must be a positive integer in the currency's smallest unit.")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise PaymentRejected("Currency must be an uppercase three-letter code.")
    if currency in {"KWD", "BHD", "OMR"} and amount % 10:
        raise PaymentRejected("Three-decimal currency amounts must end in zero.")


def _merchant(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise PaymentRejected("A safe merchant reference is required.")


def _eligible(payment: PaymentSnapshot, amount: int, currency: str) -> None:
    if payment.status != "captured":
        raise PaymentRejected("Only captured payments can be refunded.")
    if payment.currency != currency:
        raise PaymentRejected("Refund currency does not match the original payment.")
    if amount > payment.amount - payment.amount_refunded:
        raise PaymentRejected("Refund exceeds the remaining payment balance.")


def _same_request(refund: RefundSnapshot, amount: int, currency: str) -> RefundSnapshot:
    if refund.amount != amount or refund.currency != currency:
        raise PaymentRejected("This operation ID already belongs to a different refund request.")
    return refund


class RazorpayGateway:
    """Razorpay TEST credentials only; injected HTTP clients remain caller-owned."""

    mode = "razorpay_test"
    _BASE_URL = "https://api.razorpay.com/v1"
    _PAGE_SIZE = 100
    _MAX_PAGES = 5

    def __init__(self, key_id: str, key_secret: str, merchant_ref: str,
                 client: httpx.AsyncClient | None = None):
        if not isinstance(key_id, str) or not re.fullmatch(r"rzp_test_[A-Za-z0-9]+", key_id):
            raise PaymentRejected("Razorpay TEST credentials are required; live keys are disabled.")
        if not isinstance(key_secret, str) or not key_secret or any(c.isspace() for c in key_secret):
            raise PaymentRejected("A valid Razorpay test key secret is required.")
        _merchant(merchant_ref)
        self.merchant_ref = merchant_ref
        self.account_fingerprint = hashlib.sha256(key_id.encode()).hexdigest()
        self._auth = httpx.BasicAuth(key_id, key_secret)
        self._client = client

    def close(self) -> None:
        """No retained owned client; the owner closes an injected client."""

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        mutation = method != "GET"
        # Deadline also bounds custom/injected transports, not only real sockets.
        try:
            async with asyncio.timeout(12):
                for attempt in range(1 if mutation else 2):
                    try:
                        options = dict(auth=self._auth, timeout=5.0,
                                       follow_redirects=False, **kwargs)
                        if self._client is None:
                            async with httpx.AsyncClient(trust_env=False) as client:
                                response = await client.request(method, self._BASE_URL + path, **options)
                        else:
                            response = await self._client.request(method, self._BASE_URL + path, **options)
                    except httpx.HTTPError:
                        if mutation:
                            raise PaymentAmbiguous("Refund response was lost; reconciliation is required.") from None
                        if attempt == 0:
                            continue
                        raise PaymentUnavailable("Payment service could not be reached.") from None
                    status = response.status_code
                    if status in {408, 409, 429} or status >= 500:
                        if mutation:
                            raise PaymentAmbiguous("Refund outcome is uncertain; reconciliation is required.")
                        if attempt == 0:
                            continue
                        raise PaymentUnavailable("Payment service is temporarily unavailable.")
                    if not 200 <= status < 300:
                        if mutation and 400 <= status < 500:
                            raise PaymentRejected("Payment service rejected the refund request.")
                        error = PaymentAmbiguous if mutation else PaymentUnavailable
                        raise error("Payment service did not return a usable response.")
                    try:
                        payload = response.json()
                    except (ValueError, UnicodeError):
                        payload = None
                    if not isinstance(payload, dict):
                        error = PaymentAmbiguous if mutation else PaymentUnavailable
                        raise error("Payment service returned an invalid response.")
                    return payload
        except TimeoutError:
            error = PaymentAmbiguous if mutation else PaymentUnavailable
            raise error("Payment service deadline exceeded.") from None
        raise PaymentUnavailable("Payment service is unavailable.")

    def _payment(self, data: dict, payment_id: str) -> PaymentSnapshot:
        try:
            _money(data["amount"], data["currency"])
            refunded = data["amount_refunded"]
            if (data["id"] != payment_id or data.get("entity") != "payment"
                    or data["status"] not in _PAYMENT_STATES
                    or type(refunded) is not int or not 0 <= refunded <= data["amount"]
                    or (data["status"] == "captured" and data.get("captured") is not True)
                    or (data["status"] == "refunded" and refunded != data["amount"])):
                raise ValueError
            return PaymentSnapshot(payment_id, self.merchant_ref, data["status"],
                                   data["amount"], data["currency"], refunded)
        except (KeyError, TypeError, ValueError, PaymentRejected):
            raise PaymentUnavailable("Payment service returned inconsistent payment data.") from None

    @staticmethod
    def _refund(data: dict, payment_id: str) -> RefundSnapshot:
        try:
            _identifier(data["id"], "rfnd")
            _money(data["amount"], data["currency"])
            notes = data.get("notes")
            # Other merchant-created refunds may legitimately have no marker.
            if notes == [] or notes is None:
                notes = {}
            if not isinstance(notes, dict):
                raise ValueError
            operation = notes.get("quickfix_operation", "")
            if operation:
                _operation(operation)
            if (data.get("entity") != "refund" or data["payment_id"] != payment_id
                    or data["status"] not in _REFUND_STATES or not isinstance(operation, str)):
                raise ValueError
            return RefundSnapshot(data["id"], payment_id, data["amount"],
                                  data["currency"], data["status"], operation)
        except (KeyError, TypeError, ValueError, PaymentRejected):
            raise PaymentUnavailable("Payment service returned inconsistent refund data.") from None

    async def get_payment(self, payment_id: str) -> PaymentSnapshot:
        _identifier(payment_id, "pay")
        return self._payment(await self._request("GET", f"/payments/{payment_id}"), payment_id)

    async def get_refund(self, payment_id: str, refund_id: str) -> RefundSnapshot:
        _identifier(payment_id, "pay")
        _identifier(refund_id, "rfnd")
        data = await self._request("GET", f"/payments/{payment_id}/refunds/{refund_id}")
        refund = self._refund(data, payment_id)
        if refund.id != refund_id:
            raise PaymentUnavailable("Payment service returned a different refund.")
        return refund

    async def find_refund(self, payment_id: str, operation_id: str) -> RefundSnapshot | None:
        _identifier(payment_id, "pay")
        _operation(operation_id)
        found = None
        seen = set()
        try:
            async with asyncio.timeout(25):
                for page in range(self._MAX_PAGES):
                    data = await self._request("GET", f"/payments/{payment_id}/refunds",
                                               params={"count": self._PAGE_SIZE, "skip": page * self._PAGE_SIZE})
                    items = data.get("items")
                    if (data.get("entity") != "collection" or not isinstance(items, list)
                            or type(data.get("count")) is not int or data["count"] != len(items)
                            or len(items) > self._PAGE_SIZE):
                        raise PaymentUnavailable("Refund collection could not be verified.")
                    for item in items:
                        if not isinstance(item, dict):
                            raise PaymentUnavailable("Refund collection could not be verified.")
                        refund = self._refund(item, payment_id)
                        if refund.id in seen:
                            raise PaymentUnavailable("Refund pagination changed; reconcile again.")
                        seen.add(refund.id)
                        if refund.operation_id == operation_id:
                            if found is not None:
                                raise PaymentUnavailable("Multiple refunds share this operation; review is required.")
                            found = refund
                    if len(items) < self._PAGE_SIZE:
                        return found
        except TimeoutError:
            raise PaymentUnavailable("Refund reconciliation deadline exceeded.") from None
        # A truncated scan must never be interpreted as proof of absence.
        raise PaymentUnavailable("Refund history exceeds the safe scan limit; review is required.")

    async def _reconcile(self, payment_id: str, amount: int, currency: str,
                         operation_id: str) -> RefundSnapshot:
        try:
            result = await self.find_refund(payment_id, operation_id)
            if result is not None and result.amount == amount and result.currency == currency:
                return result
        except (PaymentUnavailable, PaymentRejected):
            pass
        raise PaymentAmbiguous("Refund may exist; reconcile the original operation before retrying.") from None

    async def create_refund(self, payment_id: str, amount: int, currency: str,
                            operation_id: str) -> RefundSnapshot:
        _identifier(payment_id, "pay")
        _operation(operation_id)
        _money(amount, currency)
        existing = await self.find_refund(payment_id, operation_id)
        if existing is not None:
            return _same_request(existing, amount, currency)
        _eligible(await self.get_payment(payment_id), amount, currency)
        try:
            data = await self._request("POST", f"/payments/{payment_id}/refund",
                headers={"X-Refund-Idempotency": operation_id},
                json={"amount": amount, "speed": "normal", "notes": {"quickfix_operation": operation_id}})
            created = self._refund(data, payment_id)
            verified = await self.get_refund(payment_id, created.id)
            if (created.amount != amount or created.currency != currency
                    or created.operation_id != operation_id or verified.amount != amount
                    or verified.currency != currency or verified.operation_id != operation_id):
                raise PaymentAmbiguous("Refund readback did not match the request.")
            return verified
        except (PaymentAmbiguous, PaymentUnavailable):
            return await self._reconcile(payment_id, amount, currency, operation_id)


class SimulatedGateway:
    """Durable local simulation. These records never represent transferred money."""

    mode = "simulated"

    def __init__(self, path: str | Path, merchant_ref: str = "demo-pharmacy"):
        _merchant(merchant_ref)
        self.merchant_ref = merchant_ref
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), timeout=5)
        self._db.row_factory = sqlite3.Row
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS quickfix_sim_payments (
                merchant_ref TEXT NOT NULL, id TEXT NOT NULL, status TEXT NOT NULL,
                amount INTEGER NOT NULL, currency TEXT NOT NULL, amount_refunded INTEGER NOT NULL,
                PRIMARY KEY (merchant_ref, id));
            CREATE TABLE IF NOT EXISTS quickfix_sim_refunds (
                merchant_ref TEXT NOT NULL, id TEXT NOT NULL, payment_id TEXT NOT NULL,
                amount INTEGER NOT NULL, currency TEXT NOT NULL, status TEXT NOT NULL,
                operation_id TEXT NOT NULL, PRIMARY KEY (merchant_ref, id),
                UNIQUE (merchant_ref, operation_id));
        """)

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _payment(row) -> PaymentSnapshot:
        return PaymentSnapshot(row["id"], row["merchant_ref"], row["status"], row["amount"],
                               row["currency"], row["amount_refunded"])

    @staticmethod
    def _refund(row) -> RefundSnapshot:
        return RefundSnapshot(row["id"], row["payment_id"], row["amount"], row["currency"],
                              row["status"], row["operation_id"])

    def _get_payment(self, payment_id: str) -> PaymentSnapshot:
        row = self._db.execute("SELECT * FROM quickfix_sim_payments WHERE merchant_ref=? AND id=?",
                               (self.merchant_ref, payment_id)).fetchone()
        if row is None:
            raise PaymentRejected("Simulated payment was not found for this merchant.")
        return self._payment(row)

    def seed_payment(self, payment_id: str, amount: int, currency: str = "INR", *,
                     status: str = "captured", amount_refunded: int = 0) -> PaymentSnapshot:
        _identifier(payment_id, "pay")
        _money(amount, currency)
        if (status not in _PAYMENT_STATES or type(amount_refunded) is not int
                or not 0 <= amount_refunded <= amount
                or (status == "refunded" and amount_refunded != amount)):
            raise PaymentRejected("Invalid simulated payment state.")
        try:
            with self._db:
                self._db.execute("INSERT OR IGNORE INTO quickfix_sim_payments VALUES(?,?,?,?,?,?)",
                                 (self.merchant_ref, payment_id, status, amount, currency, amount_refunded))
                existing = self._get_payment(payment_id)
                if existing.amount != amount or existing.currency != currency:
                    raise PaymentRejected("Existing simulated payment has a different amount or currency.")
                # Re-seeding on startup never resets previous refunds.
                return existing
        except sqlite3.Error:
            raise PaymentUnavailable("Simulated payment storage is unavailable.") from None

    async def get_payment(self, payment_id: str) -> PaymentSnapshot:
        _identifier(payment_id, "pay")
        try:
            return self._get_payment(payment_id)
        except sqlite3.Error:
            raise PaymentUnavailable("Simulated payment storage is unavailable.") from None

    async def find_refund(self, payment_id: str, operation_id: str) -> RefundSnapshot | None:
        _identifier(payment_id, "pay")
        _operation(operation_id)
        try:
            row = self._db.execute("SELECT * FROM quickfix_sim_refunds WHERE merchant_ref=? AND operation_id=?",
                                   (self.merchant_ref, operation_id)).fetchone()
            if row is not None and row["payment_id"] != payment_id:
                raise PaymentRejected("This operation ID belongs to another payment.")
            return self._refund(row) if row else None
        except sqlite3.Error:
            raise PaymentUnavailable("Simulated refund storage is unavailable.") from None

    async def get_refund(self, payment_id: str, refund_id: str) -> RefundSnapshot:
        _identifier(payment_id, "pay")
        _identifier(refund_id, "rfnd")
        try:
            row = self._db.execute("SELECT * FROM quickfix_sim_refunds WHERE merchant_ref=? AND payment_id=? AND id=?",
                                   (self.merchant_ref, payment_id, refund_id)).fetchone()
            if row is None:
                raise PaymentRejected("Simulated refund was not found for this payment.")
            return self._refund(row)
        except sqlite3.Error:
            raise PaymentUnavailable("Simulated refund storage is unavailable.") from None

    async def create_refund(self, payment_id: str, amount: int, currency: str,
                            operation_id: str) -> RefundSnapshot:
        _identifier(payment_id, "pay")
        _operation(operation_id)
        _money(amount, currency)
        try:
            with self._db:
                self._db.execute("BEGIN IMMEDIATE")
                # No awaits inside the transaction: balance and operation update atomically.
                row = self._db.execute("SELECT * FROM quickfix_sim_refunds WHERE merchant_ref=? AND operation_id=?",
                                       (self.merchant_ref, operation_id)).fetchone()
                if row:
                    if row["payment_id"] != payment_id:
                        raise PaymentRejected("This operation ID belongs to another payment.")
                    return _same_request(self._refund(row), amount, currency)
                payment = self._get_payment(payment_id)
                _eligible(payment, amount, currency)
                result = RefundSnapshot("rfnd_sim" + uuid4().hex, payment_id, amount, currency,
                                        "processed", operation_id)
                self._db.execute("INSERT INTO quickfix_sim_refunds VALUES(?,?,?,?,?,?,?)",
                    (self.merchant_ref, result.id, payment_id, amount, currency, result.status, operation_id))
                refunded = payment.amount_refunded + amount
                status = "refunded" if refunded == payment.amount else "captured"
                self._db.execute("UPDATE quickfix_sim_payments SET amount_refunded=?,status=? WHERE merchant_ref=? AND id=?",
                                  (refunded, status, self.merchant_ref, payment_id))
            return result
        except sqlite3.Error:
            raise PaymentUnavailable("Simulated refund transaction was not saved.") from None
