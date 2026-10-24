"""Direct merchant configuration is explicit, no-I/O and separate from demos."""
import asyncio
import json
from pathlib import Path

import pytest
from uagents_core.identity import Identity

from orderfix.commerce import runtime
from orderfix.commerce.payments import PaymentRejected, PaymentUnavailable
from orderfix.commerce.ui import render_view


def configured(tmp_path, gateway="razorpay_live"):
    return {
        "QUICKFIX_DB": str(tmp_path / "merchant.sqlite3"),
        "QUICKFIX_GATEWAY": gateway,
        "QUICKFIX_MERCHANT_REF": "authorized-pharmacy-001",
        "QUICKFIX_PHARMACY_MODE": "connected", "QUICKFIX_MAIL_MODE": "connected",
        "MEDGUARDIAN_AGENT_ADDRESS": Identity.from_seed("merchant-runtime-pharmacy-test", 0).address,
        "MAIL_AGENT_ADDRESS": Identity.from_seed("merchant-runtime-mail-test", 0).address,
        "RAZORPAY_KEY_ID": "rzp_live_privatevalue", "RAZORPAY_KEY_SECRET": "private-secret",
    }


def test_live_defaults_to_disabled_refunds_and_readiness_never_contacts_gateway(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Readiness must not construct a gateway")

    monkeypatch.setattr(runtime, "RazorpayGateway", forbidden)
    env = configured(tmp_path)
    settings = runtime.CommerceSettings.load(env)
    settings.validate()
    assert settings.live_refunds_enabled is False
    status = runtime.status_from_env(env)
    assert status["ready"] is True
    assert status["gateway_razorpay_live"] is True
    assert status["live_refunds_enabled"] is False
    assert all(type(value) is bool for value in status.values())
    assert "private" not in json.dumps(status)
    assert "private" not in repr(settings)
    assert not Path(env["QUICKFIX_DB"]).exists()


@pytest.mark.parametrize("change", [
    {"QUICKFIX_AUTO_DEMO": "true"},
    {"QUICKFIX_PHARMACY_MODE": "simulated"},
    {"QUICKFIX_MAIL_MODE": "captured"},
    {"QUICKFIX_ENABLE_LIVE_REFUNDS": "yes"},
    {"QUICKFIX_MERCHANT_REF": ""},
    {"QUICKFIX_MERCHANT_REF": "demo-pharmacy"},
    {"QUICKFIX_MERCHANT_REF": "test-pharmacy"},
    {"QUICKFIX_MERCHANT_REF": "sim_merchant"},
    {"RAZORPAY_KEY_ID": "rzp_test_wrongmode"},
    {"RAZORPAY_KEY_SECRET": ""},
    {"MAIL_AGENT_ADDRESS": "not-an-agent"},
])
def test_invalid_live_configuration_waits_without_creating_data(tmp_path, monkeypatch, change):
    def forbidden(*args, **kwargs):
        raise AssertionError("Incomplete setup must not construct a provider")

    monkeypatch.setattr(runtime, "RazorpayGateway", forbidden)
    monkeypatch.setattr(runtime, "SimulatedGateway", forbidden)
    env = {**configured(tmp_path), **change}
    engine = runtime.make_connected_engine(env)
    assert isinstance(engine, runtime.WaitingCommerce)
    assert not Path(env["QUICKFIX_DB"]).exists()


@pytest.mark.parametrize("gateway", ["simulated", "razorpay_test", "unconfigured"])
def test_live_refund_switch_cannot_enable_another_gateway(tmp_path, gateway):
    env = {**configured(tmp_path, gateway), "QUICKFIX_ENABLE_LIVE_REFUNDS": "true"}
    with pytest.raises(runtime.CommerceConfigurationError):
        runtime.CommerceSettings.load(env)


@pytest.mark.parametrize("gateway", ["simulated", "razorpay_test"])
def test_connected_factory_never_falls_back_to_test_providers(tmp_path, monkeypatch, gateway):
    monkeypatch.setattr(runtime, "SimulatedGateway", lambda *_: pytest.fail("No simulator allowed"))
    monkeypatch.setattr(runtime, "RazorpayGateway", lambda *_, **__: pytest.fail("No test gateway allowed"))
    assert isinstance(runtime.make_connected_engine(configured(tmp_path, gateway)), runtime.WaitingCommerce)
    assert not (tmp_path / "merchant.sqlite3").exists()


@pytest.mark.parametrize("enabled", [False, True])
def test_factory_passes_live_mode_and_explicit_write_switch(tmp_path, monkeypatch, enabled):
    received = {}

    class Gateway:
        mode = "razorpay_live"
        merchant_ref = "authorized-pharmacy-001"

        def __init__(self, key_id, secret, merchant_ref, **kwargs):
            received.update(kwargs)
            self.live_refunds_enabled = kwargs["allow_live_refunds"]

        def close(self):
            pass

    monkeypatch.setattr(runtime, "RazorpayGateway", Gateway)
    env = {**configured(tmp_path), "QUICKFIX_ENABLE_LIVE_REFUNDS": str(enabled).lower()}
    engine = runtime.make_connected_engine(env)
    try:
        assert not isinstance(engine, runtime.WaitingCommerce)
        assert received == {"mode": "razorpay_live", "allow_live_refunds": enabled}
        assert engine.auto_demo is False
        assert engine.pharmacy_mode == engine.mail_mode == "connected"
    finally:
        engine.store.close()
        engine.gateway.close()


def test_unconfigured_gateway_allows_real_order_engine_without_synthetic_payments(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No payment provider should be constructed")

    monkeypatch.setattr(runtime, "RazorpayGateway", forbidden)
    monkeypatch.setattr(runtime, "SimulatedGateway", forbidden)
    env = configured(tmp_path, "unconfigured")
    env.pop("RAZORPAY_KEY_ID")
    env.pop("RAZORPAY_KEY_SECRET")
    engine = runtime.make_connected_engine(env)
    try:
        assert isinstance(engine.gateway, runtime.UnconfiguredGateway)
        assert engine.gateway.merchant_ref == "authorized-pharmacy-001"
        assert engine.gateway.live_refunds_enabled is False
        assert engine.auto_demo is False
        with pytest.raises(PaymentUnavailable):
            asyncio.run(engine.gateway.get_payment("pay_merchant"))
        with pytest.raises(PaymentUnavailable):
            asyncio.run(engine.gateway.find_refund("pay_merchant", "operation-id"))
        with pytest.raises(PaymentRejected):
            asyncio.run(engine.gateway.create_refund("pay_merchant", 500, "INR", "operation-id"))
        assert engine.store.conn.execute("SELECT COUNT(*) FROM qf_bindings").fetchone()[0] == 0
        assert not engine.store.conn.execute("SELECT name FROM sqlite_master WHERE name LIKE '%payment%'").fetchall()
    finally:
        engine.store.close()
        engine.gateway.close()


@pytest.mark.parametrize("message,language", [("show my orders", "en"), ("मेरे ऑर्डर दिखाओ", "hi"), ("ನನ್ನ ಆರ್ಡರ್ ತೋರಿಸಿ", "kn")])
def test_waiting_response_has_no_order_records_no_actions_and_correct_language(message, language):
    engine = runtime.make_connected_engine({})
    assert isinstance(engine, runtime.WaitingCommerce)
    assert engine.handles("sender", "session", message)
    view = asyncio.run(engine.handle("sender", "session", "message", message))
    assert view["language"] == language
    assert view["code"] == "connection_required"
    assert not hasattr(engine, "store")
    assert not hasattr(engine, "gateway")
    text, card = render_view(view, language)
    assert card is None
    assert "INR" not in text
    if language == "en":
        assert "authorized connection pending" in text
        assert "Pharmacy: connected service" not in text
        assert "Email: connected service" not in text


@pytest.mark.parametrize("language,total_label", [("en", "Order total"), ("hi", "ऑर्डर की कुल राशि"), ("kn", "ಆರ್ಡರ್ ಒಟ್ಟು ಮೊತ್ತ")])
def test_real_order_detail_shows_exact_total_but_no_refund_approval(language, total_label):
    view = {
        "kind": "order", "gateway_mode": "unconfigured", "pharmacy_mode": "connected", "mail_mode": "connected",
        "title": "Source order", "pharmacy": "Authorized pharmacy", "amount_minor": 123456,
        "currency": "INR", "order_state": "confirmed", "token": "safe-opaque-token",
        "items": [{"name": "Original product", "strength": "original strength", "form": "original form", "quantity": 2}],
    }
    text, card = render_view(view, language)
    assert total_label in text and "INR 1,234.56" in text
    payload = json.loads(card.metadata["card_payload"])
    assert {cta["selection"]["action"] for cta in payload["ctas"]} == {"qf_status", "qf_back"}
    assert "approve_cta" not in payload
    assert "qf_approve" not in json.dumps(payload)
    assert "original strength" in text


def test_readonly_live_mode_is_not_labelled_simulated_or_refund_enabled():
    base = {"kind": "orders", "gateway_mode": "razorpay_live", "pharmacy_mode": "connected", "mail_mode": "connected",
            "refunds_enabled": False, "orders": [{"title": "Original order", "token": "order-token", "amount_minor": 38975, "currency": "INR"}]}
    text, card = render_view(base)
    assert "Razorpay live account; refunds are disabled" in text
    assert "simulation" not in text and "test mode" not in text
    assert json.loads(card.metadata["card_payload"])["items"][0]["primary_cta"]["label"] == "View order"
    help_text, help_card = render_view({**base, "kind": "help"})
    assert "Cancellation and refunds are currently disabled" in help_text
    assert help_card is None
