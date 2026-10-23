"""Persistent synthetic merchant backend for the no-cost demonstration.

All orders and return requests are LOCAL DEMO records, not Shopify transactions.
Each verified demonstration customer has a separate namespace. Return creation
revalidates current quantities and policy in a transaction, then reads the record
back after committing. This backend never moves money or issues refunds.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from .models import AmbiguousAction, LineItem, Order, ProviderError, Reason, ReturnRecord, utcnow
from .policy import ReturnPolicy


class DemoProvider:
    mode = "demo"

    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._db = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA busy_timeout = 30000")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS demo_customers (
                customer_ref TEXT PRIMARY KEY,
                seeded_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS demo_orders (
                id TEXT PRIMARY KEY,
                customer_ref TEXT NOT NULL REFERENCES demo_customers(customer_ref),
                number TEXT NOT NULL,
                fulfillment_status TEXT NOT NULL,
                fulfilled_at TEXT,
                currency TEXT NOT NULL,
                total TEXT NOT NULL,
                UNIQUE(customer_ref, number)
            );
            CREATE TABLE IF NOT EXISTS demo_order_lines (
                id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL REFERENCES demo_orders(id),
                title TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity > 0),
                excluded INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS demo_returns (
                id TEXT PRIMARY KEY,
                number TEXT NOT NULL UNIQUE,
                customer_ref TEXT NOT NULL REFERENCES demo_customers(customer_ref),
                order_id TEXT NOT NULL REFERENCES demo_orders(id),
                line_id TEXT NOT NULL REFERENCES demo_order_lines(id),
                quantity INTEGER NOT NULL CHECK(quantity > 0),
                reason TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                UNIQUE(customer_ref, operation_id)
            );
            CREATE INDEX IF NOT EXISTS demo_returns_order_idx ON demo_returns(order_id);
        """)
        self._policy = ReturnPolicy()

    def _ensure_open(self) -> None:
        if self._closed:
            raise ProviderError("The demonstration store is closed.")

    @staticmethod
    def _validate_customer(customer_ref: str) -> None:
        if not isinstance(customer_ref, str) or not customer_ref.strip() or len(customer_ref) > 256:
            raise ProviderError("A valid demonstration customer is required.")

    @staticmethod
    def _normalize_number(number: str) -> str:
        if not isinstance(number, str):
            return ""
        value = number.strip()
        return value if value.startswith("#") else f"#{value}"

    def _seed_customer(self, customer_ref: str) -> None:
        if self._db.execute(
            "SELECT 1 FROM demo_customers WHERE customer_ref = ?", (customer_ref,)
        ).fetchone():
            return
        seeded_at = utcnow()
        namespace = hashlib.sha256(customer_ref.encode("utf-8")).hexdigest()
        self._db.execute(
            "INSERT INTO demo_customers VALUES (?, ?)", (customer_ref, seeded_at.isoformat())
        )
        samples = [
            ("1001", "FULFILLED", 3, "4197.00", [("Wireless headphones", 2, False), ("USB-C cable", 1, False)]),
            ("1002", "FULFILLED", 30, "1999.00", [("Wireless headphones", 1, False)]),
            ("1003", "UNFULFILLED", None, "1999.00", [("Wireless headphones", 1, False)]),
            ("1004", "FULFILLED", 3, "499.00", [("Digital music voucher", 1, True)]),
            ("1005", "FULFILLED", 3, "1999.00", [("Wireless headphones", 1, False)]),
        ]
        for number, status, age, total, lines in samples:
            order_id = f"demo-order-{namespace}-{number}"
            fulfilled = (seeded_at - timedelta(days=age)).isoformat() if age is not None else None
            self._db.execute(
                "INSERT INTO demo_orders VALUES (?, ?, ?, ?, ?, ?, ?)",
                (order_id, customer_ref, f"#{number}", status, fulfilled, "INR", total),
            )
            for index, (title, quantity, excluded) in enumerate(lines, 1):
                line_id = f"{order_id}-line-{index}"
                self._db.execute(
                    "INSERT INTO demo_order_lines VALUES (?, ?, ?, ?, ?)",
                    (line_id, order_id, title, quantity, int(excluded)),
                )
            if number == "1005":
                self._db.execute(
                    "INSERT INTO demo_returns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (f"demo-return-{namespace}-seeded-1005", f"DEMO-R-{namespace[:12].upper()}-1005",
                     customer_ref, order_id, f"{order_id}-line-1", 1, "damaged", "REQUESTED",
                     (seeded_at - timedelta(days=1)).isoformat(), "demo-seeded-1005"),
                )

    @staticmethod
    def _return_record(row: sqlite3.Row) -> ReturnRecord:
        return ReturnRecord(
            id=row["id"], number=row["number"], order_id=row["order_id"],
            line_id=row["line_id"], quantity=row["quantity"], reason=row["reason"],
            status=row["status"], created_at=datetime.fromisoformat(row["created_at"]),
            operation_id=row["operation_id"], mode="demo",
        )

    def _read_order(self, number: str, customer_ref: str) -> Order | None:
        row = self._db.execute(
            "SELECT * FROM demo_orders WHERE number = ? AND customer_ref = ?", (number, customer_ref)
        ).fetchone()
        if row is None:
            return None
        records = [self._return_record(r) for r in self._db.execute(
            "SELECT * FROM demo_returns WHERE order_id = ? ORDER BY created_at, id", (row["id"],)
        ).fetchall()]
        lines = []
        for line in self._db.execute(
            "SELECT * FROM demo_order_lines WHERE order_id = ? ORDER BY id", (row["id"],)
        ).fetchall():
            reserved = sum(r.quantity for r in records if r.line_id == line["id"]
                           and r.status not in {"CANCELED", "CANCELLED", "DECLINED"})
            lines.append(LineItem(
                id=line["id"], title=line["title"], quantity=line["quantity"],
                returnable_quantity=max(0, line["quantity"] - reserved),
                returnable_id=line["id"], excluded=bool(line["excluded"]),
            ))
        return Order(
            id=row["id"], number=row["number"], customer_ref=customer_ref,
            fulfillment_status=row["fulfillment_status"],
            fulfilled_at=datetime.fromisoformat(row["fulfilled_at"]) if row["fulfilled_at"] else None,
            currency=row["currency"], total=row["total"], lines=lines, returns=records, mode="demo",
        )

    def _owned_order(self, order: Order) -> Order:
        self._validate_customer(order.customer_ref)
        current = self._read_order(self._normalize_number(order.number), order.customer_ref)
        if current is None or current.id != order.id or order.mode != "demo":
            raise ProviderError("The order does not belong to this demonstration customer.")
        return current

    async def get_order(self, number: str, customer_ref: str) -> Order | None:
        self._validate_customer(customer_ref)
        with self._lock:
            self._ensure_open()
            try:
                self._db.execute("BEGIN IMMEDIATE")
                self._seed_customer(customer_ref)
                self._db.commit()
                return self._read_order(self._normalize_number(number), customer_ref)
            except sqlite3.Error as exc:
                self._db.rollback()
                raise ProviderError("The demonstration store is temporarily unavailable.") from exc

    async def find_return(self, order: Order, operation_id: str) -> ReturnRecord | None:
        with self._lock:
            self._ensure_open()
            try:
                current = self._owned_order(order)
                row = self._db.execute(
                    "SELECT * FROM demo_returns WHERE customer_ref = ? AND order_id = ? AND operation_id = ?",
                    (current.customer_ref, current.id, operation_id),
                ).fetchone()
                return self._return_record(row) if row else None
            except sqlite3.Error as exc:
                raise ProviderError("The demonstration return status could not be read.") from exc

    async def request_return(
        self, order: Order, line_id: str, quantity: int, reason: Reason, operation_id: str
    ) -> ReturnRecord:
        if not isinstance(operation_id, str) or not operation_id.strip() or len(operation_id) > 256:
            raise ProviderError("A valid operation identifier is required.")
        if reason not in {"damaged", "wrong_item", "size", "changed_mind", "other"}:
            raise ProviderError("Choose a supported return reason.")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise ProviderError("Return quantity must be a positive whole number.")
        with self._lock:
            self._ensure_open()
            committed = False
            try:
                self._db.execute("BEGIN IMMEDIATE")
                current = self._owned_order(order)
                existing = self._db.execute(
                    "SELECT * FROM demo_returns WHERE customer_ref = ? AND operation_id = ?",
                    (current.customer_ref, operation_id),
                ).fetchone()
                if existing:
                    if (existing["order_id"], existing["line_id"], existing["quantity"], existing["reason"]) != (
                        current.id, line_id, quantity, reason
                    ):
                        raise ProviderError("This operation identifier was already used for a different return.")
                    record_id = existing["id"]
                else:
                    eligibility = self._policy.evaluate(current, line_id, quantity)
                    if not eligibility.eligible:
                        raise ProviderError(f"This return cannot be submitted: {eligibility.code}.")
                    unique_id = uuid.uuid4().hex
                    record_id = f"demo-return-{unique_id}"
                    self._db.execute(
                        "INSERT INTO demo_returns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (record_id, f"DEMO-R-{unique_id[:12].upper()}", current.customer_ref, current.id,
                         line_id, quantity, reason, "REQUESTED", utcnow().isoformat(), operation_id),
                    )
                self._db.commit()
                # Confirm the committed request by reading its persisted representation.
                committed = True
                readback = self._db.execute("SELECT * FROM demo_returns WHERE id = ?", (record_id,)).fetchone()
                if readback is None:
                    raise AmbiguousAction("The demonstration return could not be verified. Check its status before retrying.")
                return self._return_record(readback)
            except ProviderError:
                self._db.rollback()
                raise
            except sqlite3.Error as exc:
                self._db.rollback()
                if committed:
                    raise AmbiguousAction("The demonstration return was saved but could not be verified. Check its status before retrying.") from exc
                raise ProviderError("The demonstration return could not be saved. Check its status before retrying.") from exc

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._db.close()
                self._closed = True
