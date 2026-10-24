"""SQLite journal: no provider call happens inside a database transaction."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import timedelta
from pathlib import Path

from .models import CommerceCase, CommerceSession, now


class CommerceStore:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS qf_sessions(owner TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS qf_receipts(owner TEXT, message_id TEXT, body TEXT NOT NULL,
          PRIMARY KEY(owner,message_id));
        CREATE TABLE IF NOT EXISTS qf_bindings(owner TEXT PRIMARY KEY, customer_ref TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS qf_invites(hash TEXT PRIMARY KEY, customer_ref TEXT NOT NULL,
          expires TEXT NOT NULL, owner TEXT);
        CREATE TABLE IF NOT EXISTS qf_cases(id TEXT PRIMARY KEY, owner TEXT NOT NULL,
          customer_ref TEXT NOT NULL, order_id TEXT NOT NULL, stage TEXT NOT NULL, body TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS qf_one_case_per_order ON qf_cases(customer_ref,order_id)
          WHERE stage NOT IN ('draft_cancelled','rejected');
        CREATE TABLE IF NOT EXISTS qf_commands(id TEXT PRIMARY KEY, peer TEXT NOT NULL,
          body TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
          next_at REAL NOT NULL DEFAULT 0, response TEXT);
        CREATE TABLE IF NOT EXISTS qf_events(id INTEGER PRIMARY KEY AUTOINCREMENT,
          case_id TEXT, kind TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS qf_chat_delivery(owner TEXT PRIMARY KEY, fingerprint TEXT NOT NULL);
        """)

    def session(self, owner: str) -> CommerceSession:
        row = self.conn.execute("SELECT body FROM qf_sessions WHERE owner=?", (owner,)).fetchone()
        return CommerceSession.model_validate_json(row[0]) if row else CommerceSession()

    def has_session(self, owner: str) -> bool:
        return bool(self.conn.execute("SELECT 1 FROM qf_sessions WHERE owner=?", (owner,)).fetchone())

    def save_session(self, owner: str, session: CommerceSession):
        with self.conn:
            self.conn.execute("INSERT INTO qf_sessions VALUES (?,?) ON CONFLICT(owner) DO UPDATE SET body=excluded.body",
                              (owner, session.model_dump_json()))

    def receipt(self, owner: str, message_id: str) -> dict | None:
        row = self.conn.execute("SELECT body FROM qf_receipts WHERE owner=? AND message_id=?", (owner,message_id)).fetchone()
        return json.loads(row[0]) if row else None

    def save_receipt(self, owner: str, message_id: str, value: dict):
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO qf_receipts VALUES (?,?,?)", (owner,message_id,json.dumps(value)))

    def bind(self, owner: str, customer_ref: str):
        with self.conn:
            existing = self.bound_customer(owner)
            if existing and existing != customer_ref:
                raise ValueError("Customer binding cannot be changed in an existing conversation")
            self.conn.execute("INSERT OR IGNORE INTO qf_bindings VALUES (?,?)", (owner,customer_ref))

    def bound_customer(self, owner: str) -> str | None:
        row = self.conn.execute("SELECT customer_ref FROM qf_bindings WHERE owner=?", (owner,)).fetchone()
        return row[0] if row else None

    def issue_invite(self, customer_ref: str) -> str:
        code = secrets.token_urlsafe(24)
        with self.conn:
            self.conn.execute("INSERT INTO qf_invites VALUES (?,?,?,NULL)",
                              (hashlib.sha256(code.encode()).hexdigest(),customer_ref,(now()+timedelta(hours=2)).isoformat()))
        return code

    def redeem(self, code: str, owner: str) -> bool:
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute("SELECT * FROM qf_invites WHERE hash=?", (hashlib.sha256(code.encode()).hexdigest(),)).fetchone()
            if not row or row['expires'] < now().isoformat() or row['owner'] not in (None,owner):
                return False
            old = self.bound_customer(owner)
            if old and old != row['customer_ref']:
                return False
            self.conn.execute("UPDATE qf_invites SET owner=? WHERE hash=?", (owner,row['hash']))
            self.conn.execute("INSERT OR IGNORE INTO qf_bindings VALUES (?,?)", (owner,row['customer_ref']))
        return True

    def case(self, case_id: str) -> CommerceCase | None:
        row = self.conn.execute("SELECT body FROM qf_cases WHERE id=?", (case_id,)).fetchone()
        return CommerceCase.model_validate_json(row[0]) if row else None

    def existing_case(self, customer_ref: str, order_id: str) -> CommerceCase | None:
        row = self.conn.execute("SELECT body FROM qf_cases WHERE customer_ref=? AND order_id=? "
                                "AND stage NOT IN ('draft_cancelled','rejected')", (customer_ref,order_id)).fetchone()
        return CommerceCase.model_validate_json(row[0]) if row else None

    def save_case(self, case: CommerceCase):
        case.updated_at = now()
        with self.conn:
            self.conn.execute("INSERT INTO qf_cases VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                              "stage=excluded.stage,body=excluded.body", (case.id,case.owner,case.customer_ref,
                              case.order.order_id,case.stage,case.model_dump_json()))

    def cases(self, stages: tuple[str,...]) -> list[CommerceCase]:
        rows = self.conn.execute("SELECT body FROM qf_cases WHERE stage IN ("+','.join('?' for _ in stages)+")", stages)
        return [CommerceCase.model_validate_json(r[0]) for r in rows]

    def queue(self, peer: str, request):
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO qf_commands(id,peer,body) VALUES (?,?,?)",
                              (request.request_id,peer,request.model_dump_json()))

    def save_case_and_commands(self, case: CommerceCase, commands: list, session=None):
        """Commit a state transition and its outgoing effects atomically."""
        case.updated_at = now()
        with self.conn:
            self.conn.execute("INSERT INTO qf_cases VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                              "stage=excluded.stage,body=excluded.body", (case.id,case.owner,case.customer_ref,
                              case.order.order_id,case.stage,case.model_dump_json()))
            if session is not None:
                self.conn.execute("INSERT INTO qf_sessions VALUES (?,?) ON CONFLICT(owner) DO UPDATE SET body=excluded.body",
                                  (case.owner,session.model_dump_json()))
            for peer,request in commands:
                self.conn.execute("INSERT OR IGNORE INTO qf_commands(id,peer,body) VALUES (?,?,?)",
                                  (request.request_id,peer,request.json()))

    def renew_command(self, old_id: str, peer: str, request):
        # The effect key and payload are unchanged. Only the transport envelope
        # gets a fresh expiry; peers must return their durable cached outcome.
        with self.conn:
            self.conn.execute("UPDATE qf_commands SET state='superseded' WHERE id=?", (old_id,))
            self.conn.execute("INSERT INTO qf_commands(id,peer,body) VALUES (?,?,?)",
                              (request.request_id,peer,request.json()))

    def sessions(self):
        return [(r[0],CommerceSession.model_validate_json(r[1])) for r in
                self.conn.execute("SELECT owner,body FROM qf_sessions")]

    def delivered(self, owner: str, fingerprint: str) -> bool:
        row = self.conn.execute("SELECT fingerprint FROM qf_chat_delivery WHERE owner=?", (owner,)).fetchone()
        return bool(row and row[0] == fingerprint)

    def mark_delivered(self, owner: str, fingerprint: str):
        with self.conn:
            self.conn.execute("INSERT INTO qf_chat_delivery VALUES (?,?) ON CONFLICT(owner) DO UPDATE SET fingerprint=excluded.fingerprint",
                              (owner,fingerprint))

    def command(self, request_id: str):
        return self.conn.execute("SELECT * FROM qf_commands WHERE id=?", (request_id,)).fetchone()

    def due(self):
        return self.conn.execute("SELECT * FROM qf_commands WHERE state IN ('queued','sent') AND next_at<=? "
                                 "ORDER BY rowid LIMIT 20", (now().timestamp(),)).fetchall()

    def mark_sent(self, request_id: str):
        with self.conn:
            self.conn.execute("UPDATE qf_commands SET state='sent',attempts=attempts+1,next_at=? WHERE id=?",
                              (now().timestamp()+10,request_id))

    def finish_command(self, request_id: str, response):
        with self.conn:
            self.conn.execute("UPDATE qf_commands SET state='answered',response=? WHERE id=?",
                              (response.model_dump_json(),request_id))

    def event(self, case_id: str, kind: str):
        with self.conn:
            self.conn.execute("INSERT INTO qf_events(case_id,kind,created_at) VALUES (?,?,?)", (case_id,kind,now().isoformat()))

    def close(self):
        self.conn.close()
