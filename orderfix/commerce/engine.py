"""Durable, event-driven cancellation/refund coordinator.

Chat handlers only journal intent. Signed peer responses and a bounded worker
advance the case; a mail failure never repeats the financial operation.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from datetime import timedelta
from uuid import UUID, uuid4, uuid5

from pydantic import ValidationError

from orderfix.storage import owner_key
from .models import CommerceCase, PharmacyOrder, now
from .payments import PaymentAmbiguous, PaymentRejected, PaymentUnavailable
from .protocol import PeerRequest, PeerResponse, PeerValidationError, validate_response
from .store import CommerceStore
from .ui import classify_text, parse_action


class CommerceEngine:
    def __init__(self, store: CommerceStore, gateway, med_agent: str, mail_agent: str,
                 *, pharmacy_mode="simulated", mail_mode="captured", auto_demo=True):
        if pharmacy_mode not in ("simulated", "connected") or mail_mode not in ("captured", "connected"):
            raise ValueError("Unknown integration mode")
        if auto_demo and (gateway.mode != "simulated" or pharmacy_mode != "simulated" or mail_mode != "captured"):
            raise ValueError("Automatic synthetic identities require all-simulation mode")
        self.store, self.gateway = store, gateway
        self.med_agent, self.mail_agent = med_agent, mail_agent
        self.pharmacy_mode, self.mail_mode, self.auto_demo = pharmacy_mode, mail_mode, auto_demo
        self._worker_lock = asyncio.Lock()

    def handles(self, sender: str, session: str, text: str) -> bool:
        owner = owner_key(sender, session)
        value = text.casefold()
        return self.store.has_session(owner) or any(x in value for x in (
            '"qf_', 'quickfix', 'quick fix', 'pharmacy', 'medicine', 'medication', 'refill',
            'cancel my order', 'cancel that order', 'connect ', 'दवा', 'फार्मेसी', 'ಔಷಧ', 'ಫಾರ್ಮಸಿ'))

    def _modes(self):
        return {"gateway_mode": self.gateway.mode, "pharmacy_mode": self.pharmacy_mode, "mail_mode": self.mail_mode}

    def _binding(self):
        return hashlib.sha256(json.dumps([self._modes(), self.med_agent, self.mail_agent,
            self.gateway.merchant_ref, getattr(self.gateway, "account_fingerprint", "simulated")], sort_keys=True).encode()).hexdigest()

    def _compatible(self, case):
        if case.binding_fingerprint != self._binding():
            case.stage, case.last_error = "blocked", "INTEGRATION_CONFIGURATION_CHANGED"
            self.store.save_case(case)
            return False
        return True

    def _notice(self, code: str):
        return {"kind": "notice", "code": code, **self._modes()}

    def _case_view(self, case: CommerceCase):
        if case.stage == "rejected":
            return self._notice("ineligible")
        if case.stage == "draft_cancelled":
            return self._notice("cancelled_draft")
        common = {"title": case.order.title, "pharmacy": case.order.pharmacy_name,
                  "amount_minor": case.amount_minor if case.payment_state == "captured" else None, "currency": case.order.currency,
                  "token": case.token, "items":[item.model_dump() for item in case.order.items],
                  "gateway_mode":case.gateway_mode, "pharmacy_mode":case.pharmacy_mode, "mail_mode":case.mail_mode}
        if case.stage == "review":
            return {"kind": "review", **common, "policy_text": case.policy_text,
                    "expires_at": case.expires_at.isoformat(), "customer_email": case.order.customer_email,
                    "pharmacy_email": case.order.pharmacy_email}
        return {"kind": "status", **common, "order_state": case.order.order_state,
                "payment_state": case.payment_state, "refund_state": case.refund_state,
                "customer_notification": case.customer_notification,
                "pharmacy_notification": case.pharmacy_notification, "stage": case.stage,
                "last_error": case.last_error}

    def _view(self, owner: str):
        session = self.store.session(owner)
        case = self.store.case(session.active_case) if session.active_case else None
        # Owner binding is intentionally checked even for opaque card tokens.
        view = self._case_view(case) if case and case.owner == owner else session.view
        return {**self._modes(), **view, "language": session.language}

    def _request(self, owner: str, operation: str, payload: dict, *, case=None, key=None):
        session = self.store.session(owner)
        instant = now()
        return PeerRequest(request_id=str(uuid4()), correlation_id=owner,
            case_id=case.id if case else owner, customer_ref=session.customer_ref,
            operation=operation, idempotency_key=key or "op-"+uuid4().hex,
            created_at=instant, expires_at=instant+timedelta(minutes=10),
            expected_version=case.version if case else 0, mode="sandbox", payload=payload)

    def _queue(self, owner, operation, payload, *, case=None, key=None):
        peer = self.mail_agent if operation.startswith("mail.") else self.med_agent
        if not peer:
            return False
        request = self._request(owner, operation, payload, case=case, key=key)
        self.store.queue(peer, request)
        return True

    async def handle(self, sender: str, session_id: str, message_id: str, text: str) -> dict:
        owner = owner_key(sender, session_id)
        prior = self.store.receipt(owner, message_id)
        if prior:
            return self._view(owner)
        state = self.store.session(owner)
        state.route_sender, state.route_session = sender, session_id
        intent = classify_text(text)
        if intent.get("language"):
            state.language = intent["language"]
        bound = self.store.bound_customer(owner)
        if not bound and self.auto_demo:
            bound = "demo-"+owner[:32]
            self.store.bind(owner, bound)
        # A one-use operator-issued link code is account setup, never an order ID.
        if text.startswith("connect "):
            self.store.redeem(text[8:].strip(), owner)
            bound = self.store.bound_customer(owner)
        state.customer_ref = bound
        self.store.save_session(owner, state)
        action = parse_action(text)
        selection = [token for token, order in state.orders.items()
                     if text.strip().casefold() == order.title.casefold()]
        if not bound:
            view = self._notice("auth_required")
        elif len(text) > 4000:
            view = self._notice("invalid_action")
        elif action and action["action"] == "qf_select":
            view = self._select(owner, action.get("token", ""))
        elif action and action["action"] == "qf_approve":
            view = self._approve(owner, action.get("token"))
        elif (action and action["action"] == "qf_back") or intent["intent"] == "back" or text.strip().casefold().rstrip('.!') in ("cancel", "stop", "रद्द", "रद्द करें", "ರದ್ದು"):
            view = self._back(owner)
        elif (action and action["action"] == "qf_status") or intent["intent"] in ("status", "language"):
            view = self._view(owner)
        elif len(selection) == 1:
            view = self._select(owner, selection[0])
        elif intent["intent"] in ("orders", "help") or text.startswith("connect ") or text.strip().casefold() in ("quickfix", "quick fix"):
            state = self.store.session(owner)
            state.active_case = None
            state.view = self._notice("pending")
            self.store.save_session(owner, state)
            queued = self._queue(owner, "orders.list", {})
            view = state.view if queued else self._notice("blocked")
        else:
            view = self._notice("invalid_action")
        state = self.store.session(owner)
        state.view = view
        self.store.save_session(owner, state)
        result = {**self._modes(), **view, "language": state.language}
        self.store.save_receipt(owner, message_id, result)
        return result

    def _select(self, owner: str, token: str):
        state = self.store.session(owner)
        if not state.orders_expires or state.orders_expires <= now() or token not in state.orders:
            return self._notice("stale")
        order = state.orders[token]
        if order.customer_ref != state.customer_ref:
            return self._notice("invalid_action")
        existing = self.store.existing_case(order.customer_ref, order.order_id)
        if existing:
            # A second conversation needs its own authorized binding; it cannot
            # silently take over the first conversation's approval token.
            if existing.owner != owner:
                return self._notice("blocked")
            state.active_case = existing.id
            self.store.save_session(owner, state)
            return self._case_view(existing)
        case = CommerceCase(id="case-"+uuid4().hex, owner=owner, customer_ref=order.customer_ref,
            order=order, token=secrets.token_urlsafe(24), expires_at=now()+timedelta(minutes=10),
            cancellation_key="cancel-"+uuid4().hex, refund_key="refund-"+uuid4().hex,
            gateway_mode=self.gateway.mode, pharmacy_mode=self.pharmacy_mode, mail_mode=self.mail_mode,
            binding_fingerprint=self._binding())
        state.active_case = case.id
        request = self._request(owner, "order.eligibility", {"order_id":order.order_id}, case=case)
        self.store.save_case_and_commands(case, [(self.med_agent, request)], session=state)
        return self._notice("pending")

    def _approve(self, owner: str, token: str | None):
        session = self.store.session(owner)
        case = self.store.case(session.active_case) if session.active_case else None
        if not case or case.owner != owner or (token is not None and token != case.token):
            return self._notice("stale")
        if not self._compatible(case):
            return self._case_view(case)
        if case.stage != "review":
            return self._case_view(case)
        if case.expires_at <= now():
            case.stage, case.last_error = "draft_cancelled", "APPROVAL_EXPIRED"
            self.store.save_case(case)
            return self._notice("stale")
        case.approval_ref = "consent-"+uuid4().hex
        case.stage = "payment_recheck"
        case.version += 1
        self.store.save_case(case)
        self.store.event(case.id, "customer_approved_exact_review")
        return self._notice("pending")

    def _back(self, owner: str):
        session = self.store.session(owner)
        case = self.store.case(session.active_case) if session.active_case else None
        if case and case.owner == owner and case.stage in ("eligibility", "payment_check", "review"):
            case.stage = "draft_cancelled"
            case.version += 1
            self.store.save_case(case)
        elif case and case.approval_ref:
            # A chat navigation action cannot undo a submitted cancellation.
            return self._case_view(case)
        session.active_case = None
        self.store.save_session(owner, session)
        return self._notice("cancelled_draft")

    async def send_due(self, send):
        for row in self.store.due():
            request = PeerRequest.parse_raw(row["body"])
            case = self.store.case(request.case_id)
            if case and not self._compatible(case):
                continue
            if case and request.operation.startswith("mail.") and self._mail_context(request).get("refund", {}).get("status") != case.refund_state:
                with self.store.conn:
                    self.store.conn.execute("UPDATE qf_commands SET state='superseded' WHERE id=?", (request.request_id,))
                continue
            if request.expires_at <= now():
                case = self.store.case(request.case_id)
                applicable = not case or (request.operation.startswith("mail.") and
                    self._mail_context(request).get("refund", {}).get("status") == case.refund_state) or (
                    request.operation == "order.cancel" and case.stage == "cancelling") or (
                    request.operation == "order.eligibility" and case.stage == "eligibility")
                if applicable:
                    instant = now()
                    renewed = PeerRequest(**{**request.dict(), "request_id":str(uuid4()),
                        "created_at":instant, "expires_at":instant+timedelta(minutes=10)})
                    self.store.renew_command(request.request_id, row["peer"], renewed)
                else:
                    with self.store.conn:
                        self.store.conn.execute("UPDATE qf_commands SET state='expired' WHERE id=?", (request.request_id,))
                continue
            self.store.mark_sent(request.request_id)
            try:
                await send(row["peer"], request)
            except Exception:
                # The same durable command will be retried; never infer failure
                # of the remote effect from transport delivery failure.
                pass

    async def receive(self, sender: str, response: PeerResponse):
        row = self.store.command(response.request_id)
        if not row:
            return False
        request = PeerRequest.parse_raw(row["body"])
        try:
            validate_response(response, request, sender, {row["peer"]})
        except PeerValidationError:
            return False
        if row["state"] in ("answered", "expired", "superseded"):
            return True
        owner = request.correlation_id
        session = self.store.session(owner)
        if session.customer_ref != request.customer_ref:
            return False
        case = self.store.case(request.case_id)
        if case and (case.owner != owner or case.customer_ref != request.customer_ref):
            return False
        if case and not self._compatible(case):
            return False
        if case and request.operation.startswith("mail.") and self._mail_context(request).get("refund", {}).get("status") != case.refund_state:
            self.store.finish_command(response.request_id, response)
            return True
        if not response.ok:
            if response.error_code in ("UNAVAILABLE", "TEMPORARY_FAILURE", "RATE_LIMITED", "PROVIDER_UNAVAILABLE"):
                # Retry only this same operation. Mail failures cannot re-enter
                # the cancellation/refund state machine.
                if case:
                    case.last_error = "EMAIL_RETRY_PENDING" if request.operation.startswith("mail.") else "PEER_RETRY_PENDING"
                    self.store.save_case(case)
                self.store.mark_sent(request.request_id)
                return True
            if case:
                if request.operation.startswith("mail."):
                    role = self._mail_context(request).get("recipient_role")
                    if role in ("customer", "pharmacy"):
                        setattr(case, role+"_notification", "failed")
                    case.last_error = "EMAIL_FAILED"
                else:
                    case.stage = "rejected" if response.error_code in ("NOT_ELIGIBLE", "VERSION_CONFLICT", "ORDER_NOT_FOUND") else "blocked"
                    case.last_error = response.error_code
                self.store.save_case(case)
            else:
                session.view = self._notice("blocked")
                self.store.save_session(owner, session)
            self.store.finish_command(response.request_id, response)
            return True
        try:
            if request.operation == "orders.list":
                orders = [PharmacyOrder.model_validate(x) for x in response.payload["orders"]]
                if len(orders) > 20 or any(o.customer_ref != request.customer_ref for o in orders):
                    raise ValueError("order ownership mismatch")
                if any(o.data_mode != self.pharmacy_mode for o in orders):
                    raise ValueError("pharmacy evidence mode mismatch")
                if self.gateway.mode == "simulated" and self.pharmacy_mode == "simulated":
                    for order in orders:
                        self.gateway.seed_payment(order.payment_id, order.amount_minor, order.currency)
                session.orders = {secrets.token_urlsafe(24): order for order in orders}
                session.orders_expires = now()+timedelta(minutes=10)
                session.view = {"kind":"orders", "orders":[{"token":token,"title":order.title,
                    "pharmacy":order.pharmacy_name,"amount_minor":order.amount_minor,
                    "currency":order.currency,"order_state":order.order_state} for token,order in session.orders.items()]}
                if not orders:
                    session.view = self._notice("no_orders")
                self.store.save_session(owner, session)
            elif request.operation in ("order.eligibility", "order.cancel", "order.get"):
                if not case or case.version != request.expected_version:
                    self.store.finish_command(response.request_id, response)
                    return False
                order = PharmacyOrder.model_validate(response.payload["order"])
                if order.model_dump(exclude={"version", "order_state"}) != case.order.model_dump(exclude={"version", "order_state"}):
                    raise ValueError("immutable order fields changed")
                if request.operation == "order.eligibility" and case.stage == "eligibility":
                    if response.payload.get("eligible") is not True or order.order_state != "accepted":
                        case.stage,case.last_error = "rejected","NOT_ELIGIBLE"
                    else:
                        case.order = order
                        case.policy_text = str(response.payload.get("policy_text", ""))[:1000]
                        case.stage = "payment_check"
                    self.store.save_case(case)
                elif request.operation == "order.cancel" and case.stage == "cancelling":
                    if response.payload.get("cancelled") is not True or order.order_state != "cancelled":
                        case.stage,case.last_error = "blocked","CANCELLATION_NOT_VERIFIED"
                    else:
                        case.order,case.stage = order,"refund"
                        self.store.event(case.id,"pharmacy_cancellation_verified")
                    self.store.save_case(case)
            elif request.operation.startswith("mail.") and case:
                mail_context = self._mail_context(request)
                role = mail_context.get("recipient_role")
                state = response.payload.get("state")
                if role not in ("customer", "pharmacy") or state not in ("captured","queued","accepted","delivered","failed","bounced"):
                    raise ValueError("invalid delivery evidence")
                if state in ("accepted","delivered") and not response.payload.get("provider_message_id"):
                    raise ValueError("missing mail provider evidence")
                if self.mail_mode == "captured" and state != "captured":
                    raise ValueError("capture cannot establish external delivery")
                if self.mail_mode == "connected" and state == "captured":
                    raise ValueError("connected service returned local capture")
                notification_id = response.payload.get("notification_id")
                if not isinstance(notification_id, str) or not 1 <= len(notification_id) <= 128:
                    raise ValueError("missing notification reference")
                if request.operation == "mail.status" and notification_id != request.payload.get("notification_id"):
                    raise ValueError("mismatched notification reference")
                # Do not let an older pending-refund notification overwrite the
                # status of the newer processed-refund notification.
                if mail_context.get("refund",{}).get("status") == case.refund_state:
                    setattr(case,role+"_notification",state)
                    if case.last_error in ("EMAIL_FAILED", "EMAIL_RETRY_PENDING"):
                        case.last_error = None
                    self.store.save_case(case)
                    if state in ("queued", "accepted"):
                        followup = self._request(owner, "mail.status", {"notification_id":notification_id}, case=case,
                            key="status-"+uuid5(UUID(request.request_id), "delivery-status").hex)
                        followup = PeerRequest(**{**followup.dict(), "request_id":str(uuid5(UUID(request.request_id), "delivery-status"))})
                        self.store.queue(self.mail_agent, followup)
                        with self.store.conn:
                            self.store.conn.execute("UPDATE qf_commands SET next_at=? WHERE id=?",
                                (now().timestamp()+30,followup.request_id))
            else:
                raise ValueError("unexpected operation")
        except (ValueError,KeyError,TypeError,ValidationError):
            if case:
                case.last_error = "INVALID_PEER_RESULT"
                self.store.save_case(case)
            return False
        self.store.finish_command(response.request_id,response)
        return True

    def _mail_context(self, request):
        if request.operation == "mail.send":
            return request.payload
        if request.operation != "mail.status":
            return {}
        row = self.store.conn.execute("SELECT body FROM qf_commands WHERE "
            "json_extract(body,'$.operation')='mail.send' AND json_extract(body,'$.case_id')=? "
            "AND json_extract(response,'$.payload.notification_id')=? ORDER BY rowid DESC LIMIT 1",
            (request.case_id,request.payload.get("notification_id"))).fetchone()
        return PeerRequest.parse_raw(row[0]).payload if row else {}

    def _check_payment(self, case: CommerceCase, payment):
        if (payment.id,payment.merchant_ref,payment.amount,payment.currency,payment.status) != (
            case.order.payment_id,case.order.merchant_ref,case.order.amount_minor,case.order.currency,"captured"):
            raise PaymentRejected("Payment does not match the verified order")
        remaining = payment.amount-payment.amount_refunded
        if remaining <= 0:
            raise PaymentRejected("No remaining refundable balance")
        return remaining

    def _save_refund(self, case: CommerceCase, refund):
        if (refund.payment_id,refund.amount,refund.currency,refund.operation_id) != (
            case.order.payment_id,case.amount_minor,case.order.currency,case.refund_key):
            raise PaymentRejected("Refund does not match the approved operation")
        changed = (case.refund_id, case.refund_state) != (refund.id, refund.status)
        case.refund_id,case.refund_state = refund.id,refund.status
        case.stage = "done" if refund.status == "processed" else "blocked" if refund.status == "failed" else "refund_pending"
        case.last_error = "REFUND_FAILED" if refund.status == "failed" else None
        self._notify(case)
        if changed:
            self.store.event(case.id, "gateway_refund_"+refund.status)

    def _notify(self, case: CommerceCase):
        requests = []
        session = self.store.session(case.owner)
        for role in ("customer","pharmacy"):
            key = f"{case.id}-{case.refund_state}-{role}"
            # One immutable notification per recipient and state transition.
            if self.store.conn.execute("SELECT 1 FROM qf_commands WHERE json_extract(body,'$.idempotency_key')=?",(key,)).fetchone():
                continue
            payload = {"event_id":case.id+"-"+case.refund_state,"recipient_role":role,
                "recipient":getattr(case.order,role+"_email"),"template_version":"1","language":session.language,
                "order":{"title":case.order.title,"pharmacy_name":case.order.pharmacy_name},
                "refund":{"amount_minor":case.amount_minor,"currency":case.order.currency,
                          "status":case.refund_state,"refund_id":case.refund_id},"order_state":case.order.order_state}
            if self.mail_agent:
                requests.append((self.mail_agent,self._request(case.owner,"mail.send",payload,case=case,key=key)))
                setattr(case,role+"_notification","queued")
            else:
                setattr(case,role+"_notification","failed")
        self.store.save_case_and_commands(case,requests)

    async def advance(self):
        if self._worker_lock.locked():
            return
        async with self._worker_lock:
            for case in self.store.cases(("payment_check","payment_recheck","refund","refund_pending")):
                if not self._compatible(case):
                    continue
                initial_stage,initial_version = case.stage,case.version
                try:
                    if case.stage in ("payment_check","payment_recheck"):
                        payment = await self.gateway.get_payment(case.order.payment_id)
                        current = self.store.case(case.id)
                        if not current or (current.stage,current.version)!=(initial_stage,initial_version):
                            continue
                        remaining = self._check_payment(case,payment)
                        if case.stage == "payment_check":
                            case.amount_minor = remaining
                            case.payment_state,case.stage = "captured","review"
                            case.expires_at = now()+timedelta(minutes=10)
                            case.version += 1
                            self.store.save_case(case)
                        else:
                            if remaining != case.amount_minor:
                                raise PaymentRejected("Refundable amount changed since approval")
                            case.stage = "cancelling"
                            request = self._request(case.owner,"order.cancel",{
                                "order_id":case.order.order_id,"expected_order_version":case.order.version,
                                "approval_ref":case.approval_ref},case=case,key=case.cancellation_key)
                            if not self.med_agent:
                                raise PaymentUnavailable("Pharmacy peer unavailable")
                            self.store.save_case_and_commands(case,[(self.med_agent,request)])
                    else:
                        # Cancellation has already been verified; the approved
                        # amount and stable refund key cannot be changed here.
                        if not case.approval_ref or case.order.order_state != "cancelled":
                            raise PaymentRejected("Cancellation and approval required")
                        refund = await self.gateway.get_refund(case.order.payment_id,case.refund_id) if case.refund_id else await self.gateway.find_refund(case.order.payment_id,case.refund_key)
                        if refund is None:
                            if case.refund_dispatched:
                                case.stage,case.refund_state = "refund_pending","pending"
                                case.last_error = "PROVIDER_PENDING_RECONCILIATION"
                                self.store.save_case(case)
                                continue
                            case.refund_state = "submitting"
                            case.refund_dispatched = True
                            self.store.save_case(case)
                            refund = await self.gateway.create_refund(case.order.payment_id,case.amount_minor,case.order.currency,case.refund_key)
                        self._save_refund(case,refund)
                except PaymentRejected:
                    case.stage,case.last_error = "blocked","PAYMENT_REJECTED"
                    if initial_stage in ("refund","refund_pending"):
                        case.refund_state = "blocked"
                    self.store.save_case(case)
                except (PaymentUnavailable,PaymentAmbiguous):
                    case.last_error = "PROVIDER_PENDING_RECONCILIATION"
                    if initial_stage in ("refund","refund_pending"):
                        case.stage,case.refund_state = "refund_pending","pending"
                    self.store.save_case(case)
