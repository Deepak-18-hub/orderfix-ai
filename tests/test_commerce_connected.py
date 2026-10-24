"""Launcher contract tests never start a mailbox or register any agent."""
import os
from types import SimpleNamespace

import pytest

from orderfix.commerce import connected, runtime
from orderfix.config import Settings


def test_launcher_preserves_identity_and_forces_separate_real_only_runtime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seed = "existing-private-coordinator-identity-for-test-only"
    original = {"QUICKFIX_ENABLED": "false", "QUICKFIX_GATEWAY": "simulated",
                "QUICKFIX_REQUIRE_REAL": "false", "QUICKFIX_AUTO_DEMO": "true",
                "QUICKFIX_PHARMACY_MODE": "simulated", "QUICKFIX_MAIL_MODE": "captured",
                "QUICKFIX_DB": "data/quickfix-judge/commerce.sqlite3",
                "QUICKFIX_MERCHANT_REF": "demo-pharmacy"}
    for key, value in original.items():
        monkeypatch.setenv(key, value)
    env_file = tmp_path / ".env"
    env_file.write_text("# unchanged private environment\n", encoding="utf-8")
    captured = {}
    agent = SimpleNamespace(address="existing-address")

    def build(settings):
        captured["settings"] = settings
        captured["env"] = dict(os.environ)
        assert isinstance(runtime.make_connected_engine(dict(os.environ)), runtime.WaitingCommerce)
        return agent

    monkeypatch.setattr(connected, "build_agent", build)
    result = connected.build_connected(Settings(seed=seed, mode="shopify", asi_key="private-key", port=8765))
    assert result.coordinator is agent
    assert captured["settings"].seed == seed
    assert captured["settings"].port == 8765
    assert captured["settings"].asi_key == ""
    assert captured["settings"].database == str(tmp_path / "data/quickfix-connected/legacy-disabled.sqlite3")
    assert captured["env"]["QUICKFIX_DB"] == str(tmp_path / "data/quickfix-connected/commerce.sqlite3")
    assert captured["env"]["QUICKFIX_ENABLED"] == captured["env"]["QUICKFIX_REQUIRE_REAL"] == "true"
    assert captured["env"]["QUICKFIX_AUTO_DEMO"] == "false"
    assert captured["env"]["QUICKFIX_GATEWAY"] == "unconfigured"
    assert captured["env"]["QUICKFIX_PHARMACY_MODE"] == captured["env"]["QUICKFIX_MAIL_MODE"] == "connected"
    assert {key: os.environ.get(key) for key in original} == original
    assert env_file.read_text(encoding="utf-8") == "# unchanged private environment\n"
    assert not (tmp_path / "data").exists()


def test_launcher_keeps_explicit_live_gateway_and_does_not_enable_refunds(tmp_path, monkeypatch):
    monkeypatch.setenv("QUICKFIX_GATEWAY", "razorpay_live")
    monkeypatch.delenv("QUICKFIX_ENABLE_LIVE_REFUNDS", raising=False)
    observed = {}

    def build(settings):
        observed.update(os.environ)
        return SimpleNamespace(address="existing-address")

    monkeypatch.setattr(connected, "build_agent", build)
    connected.build_connected(Settings(seed="existing-seed-for-merchant-runtime-test-12345"), tmp_path / "direct")
    assert observed["QUICKFIX_GATEWAY"] == "razorpay_live"
    assert observed.get("QUICKFIX_ENABLE_LIVE_REFUNDS", "false") == "false"


def test_launcher_rejects_missing_existing_seed_before_creating_any_files(tmp_path, monkeypatch):
    monkeypatch.setattr(connected, "build_agent", lambda *_: pytest.fail("Seed validation must come first"))
    with pytest.raises(ValueError, match="existing private AGENT_SEED"):
        connected.build_connected(Settings(seed=""), tmp_path / "must-not-exist")
    assert not (tmp_path / "must-not-exist").exists()


def test_launcher_restores_environment_after_construction_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("QUICKFIX_GATEWAY", "simulated")
    monkeypatch.delenv("QUICKFIX_REQUIRE_REAL", raising=False)

    def fail(settings):
        assert os.environ["QUICKFIX_REQUIRE_REAL"] == "true"
        raise RuntimeError("Construction failed")

    monkeypatch.setattr(connected, "build_agent", fail)
    with pytest.raises(RuntimeError, match="Construction failed"):
        connected.build_connected(Settings(seed="existing-seed-for-merchant-runtime-test-12345"), tmp_path)
    assert os.environ["QUICKFIX_GATEWAY"] == "simulated"
    assert "QUICKFIX_REQUIRE_REAL" not in os.environ


def test_runtime_binds_loopback_and_restores_sdk_host_even_on_failure(tmp_path):
    import uagents.asgi as asgi

    prior = asgi.HOST

    def run():
        assert asgi.HOST == "127.0.0.1"
        raise RuntimeError("Stopped test runtime")

    instance = connected.ConnectedRuntime(SimpleNamespace(run=run), tmp_path)
    with pytest.raises(RuntimeError, match="Stopped test runtime"):
        instance.run()
    assert asgi.HOST == prior
