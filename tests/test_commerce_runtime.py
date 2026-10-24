"""Configuration safeguards and an actual, isolated SDK-local demo process."""
import json
from pathlib import Path
import subprocess
import sys

import pytest
from uagents_core.identity import Identity

from orderfix.commerce import cli
from orderfix.commerce.runtime import (
    CommerceConfigurationError, CommerceSettings, make_engine_from_env, status_from_env,
)
from orderfix.commerce.store import CommerceStore


def configured(tmp_path):
    return {
        "QUICKFIX_DB": str(tmp_path / "commerce.sqlite3"),
        "MEDGUARDIAN_AGENT_ADDRESS": Identity.from_seed("runtime-pharmacy-test", 0).address,
        "MAIL_AGENT_ADDRESS": Identity.from_seed("runtime-mail-test", 0).address,
    }


def test_default_requires_explicit_demo_identity_opt_in():
    settings = CommerceSettings.load({})
    assert settings.gateway == "simulated"
    assert settings.auto_demo is False
    assert status_from_env({})["ready"] is False


@pytest.mark.parametrize("override", [
    {"QUICKFIX_GATEWAY": "razorpay_live"},
    {"QUICKFIX_PHARMACY_MODE": "guess"},
    {"QUICKFIX_MAIL_MODE": "smtp"},
    {"QUICKFIX_AUTO_DEMO": "yes"},
    {"QUICKFIX_DB": ":memory:"},
    {"QUICKFIX_AUTO_DEMO": "true", "QUICKFIX_GATEWAY": "razorpay_test"},
    {"QUICKFIX_AUTO_DEMO": "true", "QUICKFIX_PHARMACY_MODE": "connected"},
    {"QUICKFIX_AUTO_DEMO": "true", "QUICKFIX_MAIL_MODE": "connected"},
])
def test_invalid_or_mixed_auto_demo_configuration_is_rejected(override):
    with pytest.raises(CommerceConfigurationError):
        CommerceSettings.load(override)
    assert status_from_env(override) == {"configuration_valid": False, "ready": False}


def test_status_contains_booleans_only_and_never_credentials(tmp_path):
    env = configured(tmp_path)
    env.update(QUICKFIX_GATEWAY="razorpay_test", RAZORPAY_KEY_ID="rzp_test_privatekey",
               RAZORPAY_KEY_SECRET="private-secret")
    status = status_from_env(env)
    assert status["ready"] is True
    assert status["auto_demo_enabled"] is False
    assert all(type(value) is bool for value in status.values())
    assert "private" not in json.dumps(status)
    assert "private" not in repr(CommerceSettings.load(env))
    assert not Path(env["QUICKFIX_DB"]).exists()


def test_factory_validates_before_creating_database(tmp_path):
    env = configured(tmp_path)
    env.update(QUICKFIX_GATEWAY="razorpay_test", RAZORPAY_KEY_ID="rzp_live_no",
               RAZORPAY_KEY_SECRET="do-not-print")
    with pytest.raises(CommerceConfigurationError) as error:
        make_engine_from_env(env)
    assert "do-not-print" not in str(error.value)
    assert not Path(env["QUICKFIX_DB"]).exists()


def test_factory_uses_durable_gateway_and_requires_distinct_peers(tmp_path):
    env = configured(tmp_path)
    env["QUICKFIX_AUTO_DEMO"] = "true"
    engine = make_engine_from_env(env)
    try:
        assert engine.gateway.mode == "simulated"
        assert engine.auto_demo is True
        assert engine.med_agent == env["MEDGUARDIAN_AGENT_ADDRESS"]
        engine.gateway.seed_payment("pay_runtime", 100)
    finally:
        engine.store.close()
        engine.gateway.close()
    env["MAIL_AGENT_ADDRESS"] = env["MEDGUARDIAN_AGENT_ADDRESS"]
    with pytest.raises(CommerceConfigurationError, match="distinct"):
        make_engine_from_env(env)


def test_invite_is_only_issued_by_explicit_command_and_binds_one_owner(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "invite.sqlite3")
    monkeypatch.setattr(CommerceSettings, "load", classmethod(lambda cls, environ=None: cls(database=path)))
    assert cli.main(["invite", "customer-opaque-001"]) == 0
    output = capsys.readouterr().out
    code = output.strip().rsplit(" ", 1)[-1]
    store = CommerceStore(path)
    try:
        assert store.redeem(code, "first-owner") is True
        assert store.redeem(code, "another-owner") is False
        assert store.bound_customer("first-owner") == "customer-opaque-001"
    finally:
        store.close()


def test_demo_timeout_is_bounded_before_creating_any_files(tmp_path):
    for timeout in (0, 61):
        with pytest.raises(cli.DemoFailed):
            cli.run_demo(tmp_path / "demo", timeout=timeout)
    assert not (tmp_path / "demo").exists()


def test_actual_sdk_bureau_completes_synthetic_acp_workflow(tmp_path):
    output = tmp_path / "sdk-evidence"
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "orderfix.commerce.cli", "demo", "--output", str(output), "--timeout", "12"],
        cwd=project, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["completed"] is True
    assert evidence["transport"] == "sdk-local-dispatch"
    assert evidence["wire_signatures_verified"] is False
    assert evidence["separate_loopback_signature_probe"] == {
        "valid_envelope_delivered": True, "tampered_envelope_rejected": True,
        "transport": "http-loopback",
    }
    assert evidence["public_registration_performed"] is False
    assert evidence["real_funds_moved"] is False
    assert evidence["external_email_sent"] is False
    assert evidence["simulated_refund_records"] == 1
    assert evidence["captured_email_records"] == 2
    assert evidence["operations"] == {
        "orders.list": 1, "order.eligibility": 1, "order.cancel": 1, "mail.send": 2,
    }
    assert evidence["counts"]["peer_responses_received"] == 5
    assert evidence["counts"]["acp_requests_received"] >= 4
    assert evidence["final_state"]["refund_state"] == "processed"
