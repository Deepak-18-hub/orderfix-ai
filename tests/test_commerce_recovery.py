"""Recovery, consent binding and account-claim races; no external services."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import timedelta
import json
import threading

import pytest

from test_commerce_engine import Harness, MAIL, MED
from orderfix.commerce.engine import CommerceEngine
from orderfix.commerce.models import now
from orderfix.commerce.protocol import PeerRequest, response_for
from orderfix.commerce.store import CommerceStore
from orderfix.commerce.ui import render_view


def expire_pending(chat, operation):
    """Represent persisted commands surviving an outage longer than their TTL."""
    originals = []
    for row in chat.store.conn.execute("SELECT * FROM qf_commands WHERE state IN ('queued','sent')").fetchall():
        request = PeerRequest.parse_raw(row["body"])
        if request.operation != operation:
            continue
        aged = PeerRequest(**{**request.dict(), "created_at": now() - timedelta(minutes=21),
                               "expires_at": now() - timedelta(minutes=11)})
        with chat.store.conn:
            chat.store.conn.execute("UPDATE qf_commands SET body=?,next_at=0 WHERE id=?",
                                    (aged.json(), row["id"]))
        originals.append(aged)
    assert originals, "The scenario must retain an unanswered durable command"
    return originals


@pytest.mark.parametrize("lost_operation", ["order.cancel", "mail.send"])
def test_expired_lost_reply_recovers_after_restart_with_same_business_key(tmp_path, lost_operation):
    async def scenario():
        first = Harness(tmp_path)
        try:
            review = await first.review()
            first.drop_once.add(lost_operation)
            await first.approve(review)
            await first.pump()
            case_id = first.case().id
            originals = expire_pending(first, lost_operation)
            if lost_operation == "mail.send":
                assert len(first.refunds()) == 1
                assert len(first.captures()) == 2
            else:
                assert first.case().stage == "cancelling"
                assert not first.refunds()
        finally:
            first.close()
        restarted = Harness(tmp_path)
        try:
            await restarted.pump()
            case = restarted.store.case(case_id)
            assert case.stage == "done"
            assert case.order.version == 2
            assert case.refund_state == "processed"
            assert case.customer_notification == case.pharmacy_notification == "captured"
            assert len(restarted.refunds()) == 1
            assert len(restarted.captures()) == 2
            for original in originals:
                renewed = [request for _, request in restarted.requests
                           if request.idempotency_key == original.idempotency_key]
                assert len(renewed) == 1
                assert renewed[0].request_id != original.request_id
                assert renewed[0].payload == original.payload
                assert renewed[0].customer_ref == original.customer_ref
                assert renewed[0].case_id == original.case_id
                assert renewed[0].expected_version == original.expected_version
                assert renewed[0].expires_at > now()
                assert restarted.store.command(original.request_id)["state"] == "superseded"
        finally:
            restarted.close()
    asyncio.run(scenario())


def test_transient_email_response_retries_only_the_same_email(tmp_path):
    async def scenario():
        chat = Harness(tmp_path)
        try:
            normal_send = chat.send
            failed_request = None

            async def transient_once(target, request):
                nonlocal failed_request
                if target == MAIL and failed_request is None:
                    failed_request = request
                    chat.requests.append((target, request))
                    chat.inbox.append((target, response_for(request, ok=False, error_code="UNAVAILABLE")))
                else:
                    await normal_send(target, request)

            chat.send = transient_once
            review = await chat.review()
            await chat.approve(review)
            await chat.pump()
            assert failed_request is not None
            assert chat.case().refund_state == "processed"
            assert len(chat.refunds()) == 1
            assert len(chat.captures()) == 1
            chat.make_retries_due()
            await chat.pump()
            assert chat.gateway.refund_calls == 1
            assert len(chat.refunds()) == 1
            assert len(chat.captures()) == 2
            assert chat.case().customer_notification == chat.case().pharmacy_notification == "captured"
            assert sum(request.operation == "order.cancel" for _, request in chat.requests) == 1
            repeated = [request for _, request in chat.requests
                        if request.idempotency_key == failed_request.idempotency_key]
            assert len(repeated) == 2
            assert repeated[0].payload == repeated[1].payload
        finally:
            chat.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("changed_field", ["customer_email", "pharmacy_email", "medicine_strength", "data_mode"])
def test_cancel_reply_cannot_change_approved_order_details(tmp_path, changed_field):
    async def scenario():
        chat = Harness(tmp_path)
        try:
            review = await chat.review()
            original = chat.case().order.model_dump()
            normal_send = chat.send

            async def altered_reply(target, request):
                await normal_send(target, request)
                if request.operation != "order.cancel":
                    return
                for index, (peer, response) in enumerate(chat.inbox):
                    if response.request_id != request.request_id:
                        continue
                    payload = copy.deepcopy(response.payload)
                    if changed_field == "medicine_strength":
                        payload["order"]["items"][0]["strength"] = "unexpected strength"
                    elif changed_field == "data_mode":
                        payload["order"]["data_mode"] = "connected"
                    else:
                        payload["order"][changed_field] = "different@example.invalid"
                    chat.inbox[index] = (peer, response.copy(update={"payload": payload}))

            chat.send = altered_reply
            await chat.approve(review)
            await chat.pump()
            assert chat.case().last_error == "INVALID_PEER_RESULT"
            assert chat.case().order.model_dump() == original
            assert chat.case().refund_state != "processed"
            assert chat.gateway.refund_calls == 0
            assert not chat.captures()
        finally:
            chat.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_word", ["cancel", "रद्द करें", "ರದ್ದು"])
def test_product_title_selection_then_bare_cancel_closes_only_draft(tmp_path, cancel_word):
    async def scenario():
        chat = Harness(tmp_path)
        try:
            choices = await chat.orders()
            await chat.say(choices[0]["title"])
            await chat.pump()
            case_id = chat.case().id
            assert chat.case().stage == "review"
            await chat.say(cancel_word)
            await chat.say("confirm")
            await chat.pump()
            assert chat.store.case(case_id).stage == "draft_cancelled"
            assert chat.state().active_case is None
            assert not any(request.operation == "order.cancel" for _, request in chat.requests)
            assert chat.gateway.refund_calls == 0
        finally:
            chat.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("language,orders_text,status_text,approval", [
    ("en", "show my orders", "check status", "confirm"),
    ("hi", "मेरे ऑर्डर दिखाओ", "स्थिति देखें", "पुष्टि करें"),
    ("kn", "ನನ್ನ ಆರ್ಡರ್ ತೋರಿಸಿ", "ಸ್ಥಿತಿ ಪರಿಶೀಲಿಸಿ", "ದೃಢೀಕರಿಸಿ"),
])
def test_complete_workflow_and_both_receipts_preserve_language(tmp_path, language, orders_text, status_text, approval):
    async def scenario():
        chat = Harness(tmp_path)
        try:
            await chat.say(orders_text)
            await chat.pump()
            choices = chat.state().view["orders"]
            await chat.say(json.dumps({"action": "qf_select", "token": choices[0]["token"]}))
            await chat.pump()
            review = await chat.say(status_text)
            assert review["kind"] == "review"
            assert review["language"] == language
            rendered, card = render_view(review, language)
            assert rendered and card is not None
            await chat.say(approval)
            await chat.pump()
            status = await chat.say(status_text)
            assert status["language"] == language
            assert status["refund_state"] == "processed"
            assert status["customer_notification"] == status["pharmacy_notification"] == "captured"
            assert chat.gateway.refund_calls == 1
            assert len(chat.captures()) == 2
            assert {json.loads(row["payload"])["language"] for row in chat.captures()} == {language}
        finally:
            chat.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("changed_setting", ["gateway_mode", "pharmacy_mode", "mail_mode", "med_peer", "mail_peer", "merchant"])
def test_saved_approval_cannot_resume_under_changed_integration_binding(tmp_path, changed_setting):
    async def scenario():
        chat = Harness(tmp_path)
        try:
            review = await chat.review()
            await chat.approve(review)
            case = chat.case()
            assert case.stage == "payment_recheck"
            assert case.binding_fingerprint
            med, mail, pharmacy_mode, mail_mode = MED, MAIL, "simulated", "captured"
            if changed_setting == "gateway_mode":
                chat.gateway.mode = "razorpay_test"
            elif changed_setting == "merchant":
                chat.gateway.merchant_ref = "different-merchant"
            elif changed_setting == "pharmacy_mode":
                pharmacy_mode = "connected"
            elif changed_setting == "mail_mode":
                mail_mode = "connected"
            elif changed_setting == "med_peer":
                med = "another-pharmacy-peer"
            else:
                mail = "another-mail-peer"
            restarted = CommerceEngine(chat.store, chat.gateway, med, mail,
                pharmacy_mode=pharmacy_mode, mail_mode=mail_mode, auto_demo=False)
            await restarted.advance()
            assert chat.store.case(case.id).stage == "blocked"
            assert chat.store.case(case.id).last_error
            assert chat.gateway.refund_calls == 0
            assert not any(request.operation == "order.cancel" for _, request in chat.requests)
        finally:
            chat.close()
    asyncio.run(scenario())


def test_concurrent_invite_claim_binds_exactly_one_conversation(tmp_path):
    path = str(tmp_path / "invites.sqlite3")
    issuer = CommerceStore(path)
    code = issuer.issue_invite("authorized-customer")
    issuer.close()
    ready = threading.Barrier(2)
    read_overlap = threading.Barrier(2)

    class CursorGate:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchone(self):
            row = self.cursor.fetchone()
            # Force competing claims to overlap after a read where possible.
            # A write transaction acquired before reading legitimately prevents
            # the overlap, so the barrier expires without altering the result.
            try:
                read_overlap.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass
            return row

        def __getattr__(self, name):
            return getattr(self.cursor, name)

    class ConnectionGate:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, parameters=()):
            cursor = self.connection.execute(sql, parameters)
            return CursorGate(cursor) if sql.lstrip().upper().startswith("SELECT") and "QF_INVITES" in sql.upper() else cursor

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.connection, name)

    def claim(owner):
        store = CommerceStore(path)
        store.conn = ConnectionGate(store.conn)
        try:
            ready.wait(timeout=3)
            return owner, store.redeem(code, owner)
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("conversation-a", "conversation-b")))
    assert sorted(ok for _, ok in results) == [False, True]
    inspector = CommerceStore(path)
    try:
        bindings = inspector.conn.execute("SELECT owner,customer_ref FROM qf_bindings").fetchall()
        assert len(bindings) == 1
        assert bindings[0]["owner"] == next(owner for owner, ok in results if ok)
        assert bindings[0]["customer_ref"] == "authorized-customer"
    finally:
        inspector.close()
