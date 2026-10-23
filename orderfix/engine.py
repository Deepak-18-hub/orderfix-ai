from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import sqlite3
from datetime import timedelta

from pydantic import ValidationError

from .audit import event
from .intent import IntentParser
from .locales import tr
from .models import AmbiguousAction, Order, ProviderError, Reply, ReturnRecord, Review, Session, StoreProvider, utcnow
from .policy import ReturnPolicy
from .storage import Storage, owner_key


def approval(text: str) -> tuple[str, str] | None:
    """Only explicit commands/card actions can authorize a stored immutable review."""
    try:
        value = json.loads(text)
        if isinstance(value, dict) and set(value) <= {"action", "review_token"}:
            action, token = value.get("action"), str(value.get("review_token", "")).upper()
            if action in ("confirm", "cancel") and re.fullmatch(r"[A-F0-9]{12}", token):
                return action, token
    except (ValueError, TypeError):
        pass
    match = re.fullmatch(r"(?:please\s+)?(confirm|cancel|पुष्टि|रद्द|ದೃಢೀಕರಿಸಿ|ರದ್ದು)\s+([A-Fa-f0-9]{12})[.!]?", text, flags=re.I)
    if match:
        action = "confirm" if match[1].lower() in ("confirm", "पुष्टि", "ದೃಢೀಕರಿಸಿ") else "cancel"
        return action, match[2].upper()
    # Narrow planner forwarding envelopes; negation/free-form reasoning is never consent.
    match = re.fullmatch(r"(?:the user|i) (?:has |have )?(confirmed|approved|cancelled|canceled) "
                         r"(?:review |return request )?([A-Fa-f0-9]{12})[.!]?", text, flags=re.I)
    if match:
        return ("confirm" if match[1].lower() in ("confirmed", "approved") else "cancel"), match[2].upper()
    return None


def status_text(language: str, value: str) -> str:
    known = {"REQUESTED", "OPEN", "CLOSED", "DECLINED", "CANCELED", "FULFILLED", "UNFULFILLED", "PARTIALLY_FULFILLED"}
    return tr(language, "status_" + value) if value in known else tr(language, "status_other", status=value)


def review_text(review: Review, language: str) -> str:
    return tr(language, "review", token=review.token, order=review.order_number, item=review.item_title,
              quantity=review.quantity, reason=tr(language, "reason_" + review.reason),
              policy=tr(language, "policy_label", days=review.eligibility.window_days))


class Engine:
    def __init__(self, storage: Storage, provider: StoreProvider, parser: IntentParser | None = None,
                 policy: ReturnPolicy | None = None):
        self.storage, self.provider = storage, provider
        self.parser, self.policy = parser or IntentParser(), policy or ReturnPolicy()
        self._lock = asyncio.Lock()

    def _reply(self, state: Session, key: str, **values) -> Reply:
        return Reply(text=tr(state.language, key, **values), language=state.language)

    def _confirmed(self, state: Session, record: ReturnRecord) -> Reply:
        state.last_return, state.pending, state.stage = record, None, "idle"
        return Reply(text=tr(state.language, "confirmed", number=record.number,
                             status=status_text(state.language, record.status), order=state.order_number or record.order_id),
                     language=state.language, return_record=record)

    async def handle(self, sender: str, session_id: str, message_id: str, text: str) -> Reply:
        owner = owner_key(sender, session_id)
        correlation = hashlib.sha256(message_id.encode()).hexdigest()[:16]
        state = Session()
        event("message.received", correlation, "accepted")
        try:
            # Bound queueing too; do not leave ASI:One waiting indefinitely under load.
            async with asyncio.timeout(50):
                async with self._lock:
                    cached = self.storage.receipt(owner, message_id)
                    if cached:
                        event("message.replay", correlation, "cached")
                        return cached
                    state = self.storage.session(owner)
                    if not self.storage.allow(owner):
                        return self._reply(state, "rate_limited")
                    reply = await self._process(owner, state, text.strip(), correlation)
                    banner = tr(state.language, "demo_banner" if self.provider.mode == "demo" else "shopify_banner")
                    reply.text = banner + "\n\n" + reply.text
                    self.storage.save_turn(owner, message_id, state, reply)
                    event("turn.complete", correlation, "success")
                    return reply
        except (sqlite3.Error, RuntimeError):
            event("storage.failure", correlation, "failed", "sqlite")
            return self._reply(state, "storage_failed")
        except TimeoutError:
            event("turn.timeout", correlation, "unknown")
            return self._reply(state, "uncertain" if state.pending else "busy")
        except AmbiguousAction:
            event("return.request", correlation, "unknown", self.provider.mode)
            return self._reply(state, "uncertain")
        except ProviderError:
            event("tool.failure", correlation, "failed", self.provider.mode)
            return self._reply(state, "tool_failed")
        except (ValueError, ValidationError):
            event("input.failure", correlation, "invalid")
            return self._reply(state, "invalid_input")

    async def _order(self, state: Session, correlation: str) -> Order | None:
        if not state.order_number or not state.customer_ref:
            return None
        order = await self.provider.get_order(state.order_number, state.customer_ref)
        if order and order.customer_ref != state.customer_ref:
            raise ProviderError("Provider returned a different customer")
        event("order.lookup", correlation, "found" if order else "not_found", self.provider.mode)
        return order

    async def _resume_action(self, owner: str, state: Session, token: str, correlation: str) -> Reply | None:
        action = self.storage.action(owner, token)
        if not action:
            return None
        if action["state"] == "failed":
            state.pending, state.stage = None, "idle"
            return self._reply(state, "tool_failed")
        review = Review.model_validate_json(action["review"])
        # Reconciliation uses the reviewed order, not whatever the user looked up last.
        order = await self.provider.get_order(review.order_number, state.customer_ref)
        if order is None or order.customer_ref != state.customer_ref:
            return self._reply(state, "uncertain")
        found = await self.provider.find_return(order, action["operation_id"])
        if not found:
            return self._reply(state, "uncertain")
        self._verify_result(found, review, action["operation_id"])
        self.storage.finish_action(owner, token, found)
        state.order_number = review.order_number
        event("return.reconcile", correlation, "verified", self.provider.mode)
        return self._confirmed(state, found)

    def _verify_result(self, result: ReturnRecord, review: Review, operation_id: str) -> None:
        if (result.order_id != review.order_id or result.line_id != review.line_id
                or result.quantity != review.quantity or result.operation_id != operation_id
                or result.reason != review.reason or result.mode != self.provider.mode):
            raise AmbiguousAction("Return response did not match the approved action")

    async def _approve(self, owner: str, state: Session, command: str, token: str, correlation: str) -> Reply:
        recovered = await self._resume_action(owner, state, token, correlation)
        if recovered:
            return recovered
        unresolved = self.storage.unresolved(owner)
        if unresolved:
            return await self._resume_action(owner, state, unresolved["token"], correlation)
        review = state.pending
        if not review or review.token != token:
            return self._reply(state, "review_missing")
        if command == "cancel":
            state.pending, state.stage = None, "idle"
            return self._reply(state, "cancelled")
        if utcnow() - review.created_at > timedelta(minutes=15):
            state.pending, state.stage = None, "idle"
            return self._reply(state, "review_expired")
        order = await self.provider.get_order(review.order_number, state.customer_ref)
        if not order or order.customer_ref != state.customer_ref or order.id != review.order_id:
            return self._reply(state, "order_not_found")
        eligibility = self.policy.evaluate(order, review.line_id, review.quantity)
        if not eligibility.eligible:
            state.pending, state.stage = None, "idle"
            return self._reply(state, "stale_review")
        operation_id = self.storage.begin_action(owner, review)
        event("return.dispatch", correlation, "journaled", self.provider.mode)
        try:
            result = await self.provider.request_return(order, review.line_id, review.quantity, review.reason, operation_id)
            self._verify_result(result, review, operation_id)
        except AmbiguousAction:
            raise
        except ProviderError:
            self.storage.fail_action(owner, token)
            raise
        self.storage.finish_action(owner, token, result)
        event("return.request", correlation, "verified", self.provider.mode)
        return self._confirmed(state, result)

    async def _process(self, owner: str, state: Session, text: str, correlation: str) -> Reply:
        if not text or len(text) > 2500:
            return self._reply(state, "invalid_input")
        parsed_approval = approval(text)
        if parsed_approval:
            if not state.customer_ref:
                return self._reply(state, "access_required")
            return await self._approve(owner, state, *parsed_approval, correlation)
        if text.lower().startswith("access "):
            if self.provider.mode != "shopify":
                return self._reply(state, "unsupported")
            customer = self.storage.redeem_invite(text[7:].strip(), owner)
            if not customer:
                return self._reply(state, "access_invalid")
            if state.customer_ref and state.customer_ref != customer:
                return self._reply(state, "access_invalid")
            state.customer_ref = customer
            return self._reply(state, "access_ok")
        if self.provider.mode == "demo":
            state.customer_ref = "demo-" + owner
        unresolved = self.storage.unresolved(owner)
        if unresolved and state.customer_ref:
            return await self._resume_action(owner, state, unresolved["token"], correlation)
        if text.lower() == "review":
            if state.pending:
                return Reply(text=review_text(state.pending, state.language), language=state.language, review=state.pending)
            return self._reply(state, "review_missing")
        if text.lower() in ("new", "reset"):
            state.order_number, state.item_index, state.reason, state.pending, state.stage = None, None, None, None, "idle"
            state.quantity = 1
            return self._reply(state, "welcome")
        # Never let an LLM or an invalid approval sentence start a write flow.
        if re.search(r"\b(confirm|cancel|approve)\b|पुष्टि|ದೃಢೀಕರಿಸಿ|रद्द|ರದ್ದು", text, flags=re.I) or text.startswith("{"):
            return self._reply(state, "confirm_exact", token=state.pending.token if state.pending else "REVIEW_TOKEN")
        intent = await self.parser.parse(text, state.language)
        if intent.language:
            state.language = intent.language
        if intent.action == "language":
            return self._reply(state, "language_changed")
        if intent.action == "help":
            return self._reply(state, "welcome")
        if not state.customer_ref:
            return self._reply(state, "access_required")
        if intent.order_number and intent.order_number != state.order_number:
            state.order_number = intent.order_number
            state.pending, state.item_index, state.reason, state.stage = None, None, None, "idle"
            state.quantity = 1
        if not state.order_number:
            return self._reply(state, "order_needed")
        if intent.action == "status" and state.pending:
            recovered = await self._resume_action(owner, state, state.pending.token, correlation)
            if recovered:
                return recovered
        order = await self._order(state, correlation)
        if order is None:
            return self._reply(state, "order_not_found")
        if intent.action == "status":
            if not order.returns:
                return self._reply(state, "no_returns")
            rows = [tr(state.language, "return_row", number=r.number, status=status_text(state.language, r.status), quantity=r.quantity) for r in order.returns]
            return self._reply(state, "returns_heading", order=order.number, returns="\n".join(rows))
        if intent.action == "lookup":
            rows = [tr(state.language, "item_row", index=i, title=line.title, quantity=line.quantity, available=line.returnable_quantity)
                    for i, line in enumerate(order.lines, 1)]
            return self._reply(state, "order_summary", order=order.number, status=status_text(state.language, order.fulfillment_status),
                               total=order.total, currency=order.currency, items="\n".join(rows))
        wants_return = intent.action == "return" or state.stage in ("item", "reason", "review")
        if not wants_return:
            return self._reply(state, "unsupported")
        # Any change to return details invalidates the prior review immediately.
        state.pending = None
        if intent.item_index is not None:
            state.item_index = intent.item_index
        elif len(order.lines) == 1:
            state.item_index = 1
        if intent.quantity is not None:
            state.quantity = intent.quantity
        if intent.reason:
            state.reason = intent.reason
        if not state.item_index or not 1 <= state.item_index <= len(order.lines):
            state.stage = "item"
            rows = [tr(state.language, "item_row", index=i, title=line.title, quantity=line.quantity, available=line.returnable_quantity)
                    for i, line in enumerate(order.lines, 1)]
            return Reply(text=tr(state.language, "choose_item") + "\n" + "\n".join(rows), language=state.language)
        line = order.lines[state.item_index - 1]
        eligibility = self.policy.evaluate(order, line.id, state.quantity)
        event("policy.evaluate", correlation, eligibility.code, "policy")
        if not eligibility.eligible:
            state.stage = "idle"
            return self._reply(state, "policy_failed", reason=tr(state.language, eligibility.code, days=eligibility.window_days))
        if not state.reason:
            state.stage = "reason"
            return self._reply(state, "ask_reason")
        review = Review(token=secrets.token_hex(6).upper(), order_number=order.number, order_id=order.id,
                        line_id=line.id, item_title=line.title, quantity=state.quantity, reason=state.reason,
                        eligibility=eligibility, mode=self.provider.mode)
        state.pending, state.stage = review, "review"
        event("review.prepared", correlation, "awaiting_confirmation")
        return Reply(text=review_text(review, state.language), language=state.language, review=review)
