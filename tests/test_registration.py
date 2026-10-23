"""Registration contract tests; every network call is replaced with a fake."""
from __future__ import annotations

import json
import logging

import pytest
import requests
from uagents_core.config import AgentverseConfig
from uagents_core.contrib.protocols.chat import chat_protocol_spec
from uagents_core.identity import Identity
from uagents_core.protocol import ProtocolSpecification

from orderfix import register

SEED = "only-a-synthetic-registration-seed-never-used-live-123"
KEY = "synthetic-test-api-key-not-live"


@pytest.fixture(autouse=True)
def local_settings(monkeypatch, tmp_path):
    profile = tmp_path / "AGENT_PROFILE.md"
    profile.write_text("# OrderFix\nSynthetic demo store. English, Hindi and Kannada.", encoding="utf-8")
    monkeypatch.setattr(register, "PROFILE_PATH", profile)
    monkeypatch.setattr(register, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("AGENT_SEED", SEED)
    monkeypatch.setenv("STORE_MODE", "demo")
    monkeypatch.delenv("AGENTVERSE_API_KEY", raising=False)
    # An unmocked request is always a test failure, even in dry-run tests.
    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected network call")
    monkeypatch.setattr(requests, "get", forbidden)
    monkeypatch.setattr(requests, "post", forbidden)


def response(status, content):
    value = requests.Response()
    value.status_code = status
    value._content = json.dumps(content).encode()
    value.headers["content-type"] = "application/json"
    return value


def test_request_is_mailbox_with_actual_protocol_digest():
    plan = register.registration_request("public profile")
    assert plan.endpoint == AgentverseConfig().mailbox_endpoint
    assert plan.type == "mailbox" and plan.track_interactions is False
    assert plan.protocols == [ProtocolSpecification.compute_digest(chat_protocol_spec.manifest())]
    assert plan.active is True


def test_default_and_explicit_dry_run_make_no_network_call(monkeypatch, capsys):
    monkeypatch.setenv("AGENTVERSE_API_KEY", KEY)
    assert register.main([]) == 0
    assert register.main(["--dry-run"]) == 0
    output = capsys.readouterr().out
    assert Identity.from_seed(SEED, 0).address in output
    assert "no network calls" in output and KEY not in output and SEED not in output


def test_register_without_key_has_clear_error(capsys):
    assert register.main(["--register"]) == 2
    assert "AGENTVERSE_API_KEY is missing" in capsys.readouterr().out


def test_missing_seed_has_no_sdk_call(monkeypatch, capsys):
    monkeypatch.delenv("AGENT_SEED")
    assert register.main([]) == 2
    assert "stable AGENT_SEED" in capsys.readouterr().out


@pytest.mark.parametrize("already_exists", [False, True])
def test_real_command_uses_sdk_contract_without_transmitting_seed(monkeypatch, capsys, already_exists):
    monkeypatch.setenv("AGENTVERSE_API_KEY", KEY)
    calls = []
    address = Identity.from_seed(SEED, 0).address
    config = AgentverseConfig()
    def get(url, **kwargs):
        calls.append(("GET", url, kwargs))
        assert kwargs["headers"]["authorization"] == "Bearer " + KEY
        if url == f"{config.agents_api}/{address}":
            return response(200 if already_exists else 404, {})
        assert url == f"{config.identity_api}/{address}/challenge"
        return response(200, {"challenge": "ownership-challenge-for-unit-test"})
    def post(url, **kwargs):
        calls.append(("POST", url, kwargs))
        body = kwargs.get("data", "")
        assert SEED not in body and KEY not in body
        if url == config.identity_api:
            proof = json.loads(body)
            assert proof["address"] == address and proof["challenge_response"]
        elif url == config.agents_api:
            profile = json.loads(body)
            assert profile["agent_type"] == "mailbox"
            assert profile["endpoints"] == [{"url": config.mailbox_endpoint, "weight": 1}]
            assert profile["profile"]["readme"].startswith("# OrderFix")
        else:
            assert url == f"{config.almanac_api}/agents/{address}/status"
            assert json.loads(body)["is_active"] is True
        return response(200, {})
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(requests, "post", post)
    assert register.main(["--register"]) == 0
    assert any(method == "POST" and url == config.agents_api for method, url, _ in calls)
    assert any("/challenge" in url for _, url, _ in calls) is (not already_exists)
    output = capsys.readouterr().out
    assert "accepted" in output and KEY not in output and SEED not in output


def test_remote_exception_and_dependency_logs_cannot_print_credentials(monkeypatch, capsys):
    monkeypatch.setenv("AGENTVERSE_API_KEY", KEY)
    before = logging.root.manager.disable
    def fail(*args, **kwargs):
        logging.critical("credential %s %s", KEY, SEED)
        raise RuntimeError("remote message " + KEY + SEED)
    monkeypatch.setattr(register, "register_agent", fail)
    assert register.main(["--register"]) == 2
    output = capsys.readouterr()
    assert KEY not in output.out + output.err and SEED not in output.out + output.err
    assert "did not confirm" in output.out and logging.root.manager.disable == before


@pytest.mark.parametrize("status, expected", [(401, "rejected access"), (409, "conflicts with")])
def test_http_errors_are_sanitized(monkeypatch, capsys, status, expected):
    monkeypatch.setenv("AGENTVERSE_API_KEY", KEY)
    def fail(*args, **kwargs):
        error = requests.HTTPError(KEY, response=response(status, {"detail": KEY}))
        raise register.AgentverseRequestError(KEY, from_exc=error)
    monkeypatch.setattr(register, "register_agent", fail)
    assert register.main(["--register"]) == 2
    output = capsys.readouterr().out
    assert expected in output and KEY not in output


def test_registration_rejects_stale_demo_profile_for_shopify(monkeypatch, capsys):
    monkeypatch.setenv("STORE_MODE", "shopify")
    register.PROFILE_PATH.write_text("This agent currently uses a synthetic demo store.", encoding="utf-8")
    assert register.main(["--register"]) == 2
    assert "Update AGENT_PROFILE.md" in capsys.readouterr().out
