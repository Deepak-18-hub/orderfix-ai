"""Durable chat state and an action journal separate from the merchant backend."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import timedelta
from pathlib import Path

from .models import Reply, ReturnRecord, Review, Session, utcnow


def owner_key(sender: str, session_id: str) -> str:
    if not sender or not session_id or session_id == "None":
        raise ValueError("Stable authenticated sender and transport session required")
    return hashlib.sha256(json.dumps([sender, session_id]).encode()).hexdigest()


class Storage:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS of_sessions(owner TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS of_receipts(owner TEXT, message_id TEXT, body TEXT NOT NULL,
                PRIMARY KEY(owner, message_id));
            CREATE TABLE IF NOT EXISTS of_actions(owner TEXT, token TEXT, operation_id TEXT UNIQUE,
                state TEXT NOT NULL, review TEXT NOT NULL, result TEXT,
                PRIMARY KEY(owner,token));
            CREATE TABLE IF NOT EXISTS of_invites(code_hash TEXT PRIMARY KEY, customer_ref TEXT NOT NULL,
                expires_at TEXT NOT NULL, owner TEXT);
            CREATE TABLE IF NOT EXISTS of_limits(owner TEXT PRIMARY KEY, window_start REAL NOT NULL,
                count INTEGER NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS of_one_inflight ON of_actions(owner) WHERE state='inflight';
        """)

    def session(self, owner: str) -> Session:
        row = self.conn.execute("SELECT body FROM of_sessions WHERE owner=?", (owner,)).fetchone()
        return Session.model_validate_json(row[0]) if row else Session()

    def receipt(self, owner: str, message_id: str) -> Reply | None:
        row = self.conn.execute("SELECT body FROM of_receipts WHERE owner=? AND message_id=?",
                                (owner, message_id)).fetchone()
        return Reply.model_validate_json(row[0]) if row else None

    def save_turn(self, owner: str, message_id: str, state: Session, reply: Reply) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO of_sessions VALUES (?,?) ON CONFLICT(owner) "
                              "DO UPDATE SET body=excluded.body", (owner, state.model_dump_json()))
            self.conn.execute("INSERT INTO of_receipts VALUES (?,?,?)", (owner, message_id, reply.model_dump_json()))

    def allow(self, owner: str, limit: int = 30) -> bool:
        now = utcnow().timestamp()
        with self.conn:
            row = self.conn.execute("SELECT * FROM of_limits WHERE owner=?", (owner,)).fetchone()
            if not row or now - row["window_start"] >= 60:
                self.conn.execute("INSERT INTO of_limits VALUES (?,?,1) ON CONFLICT(owner) "
                                  "DO UPDATE SET window_start=excluded.window_start,count=1", (owner, now))
                return True
            if row["count"] >= limit:
                return False
            self.conn.execute("UPDATE of_limits SET count=count+1 WHERE owner=?", (owner,))
        return True

    def action(self, owner: str, token: str):
        return self.conn.execute("SELECT * FROM of_actions WHERE owner=? AND token=?", (owner, token)).fetchone()

    def unresolved(self, owner: str):
        return self.conn.execute("SELECT * FROM of_actions WHERE owner=? AND state='inflight'", (owner,)).fetchone()

    def begin_action(self, owner: str, review: Review) -> str:
        operation_id = "OF-" + secrets.token_hex(16)
        with self.conn:
            self.conn.execute("INSERT INTO of_actions VALUES (?,?,?,'inflight',?,NULL)",
                              (owner, review.token, operation_id, review.model_dump_json()))
        return operation_id

    def finish_action(self, owner: str, token: str, result: ReturnRecord) -> None:
        with self.conn:
            self.conn.execute("UPDATE of_actions SET state='done',result=? WHERE owner=? AND token=?",
                              (result.model_dump_json(), owner, token))

    def fail_action(self, owner: str, token: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE of_actions SET state='failed' WHERE owner=? AND token=?", (owner, token))

    def create_invite(self, customer_ref: str, hours: int = 2) -> str:
        if not customer_ref.startswith("gid://shopify/Customer/"):
            raise ValueError("A Shopify test customer GID is required")
        code = secrets.token_urlsafe(24)
        digest = hashlib.sha256(code.encode()).hexdigest()
        with self.conn:
            self.conn.execute("INSERT INTO of_invites VALUES (?,?,?,NULL)",
                              (digest, customer_ref, (utcnow() + timedelta(hours=hours)).isoformat()))
        return code

    def redeem_invite(self, code: str, owner: str) -> str | None:
        digest = hashlib.sha256(code.encode()).hexdigest()
        with self.conn:
            row = self.conn.execute("SELECT * FROM of_invites WHERE code_hash=?", (digest,)).fetchone()
            if not row or row["expires_at"] < utcnow().isoformat() or row["owner"] not in (None, owner):
                return None
            changed = self.conn.execute("UPDATE of_invites SET owner=? WHERE code_hash=? AND (owner IS NULL OR owner=?)",
                                        (owner, digest, owner))
            return row["customer_ref"] if changed.rowcount == 1 else None

    def close(self) -> None:
        self.conn.close()
