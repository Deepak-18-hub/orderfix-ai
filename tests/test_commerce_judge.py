"""Construct the real bridge/Bureau without starting mailbox or registration."""
import asyncio
import os

import pytest
from uagents import Agent
from uagents.dispatch import dispatcher
from uagents_core.contrib.protocols.chat import ChatMessage
from uagents_core.identity import Identity

from orderfix import agent as agent_module
from orderfix.commerce import judge, runtime as runtime_module
from orderfix.commerce.protocol import PeerRequest, PeerResponse
from orderfix.config import Settings


@pytest.fixture
def construction(monkeypatch):
    observed = {"settings": [], "commerce": [], "policies": [], "manifest_agents": []}
    # Suppress only constructor bootstrap and scheduled publication. The bridge,
    # real Agent/Bureau, typed protocols and SQLite services are constructed.
    monkeypatch.setattr("uagents.agent.get_almanac_contract", lambda *_: None)

    async def no_manifest(self, manifest):
        observed["manifest_agents"].append(self.address)

    monkeypatch.setattr(Agent, "publish_manifest", no_manifest)
    real_agent = agent_module.Agent

    def construct_agent(*args, **kwargs):
        kwargs["readme_path"] = None  # Profile file encoding is unrelated to construction.
        return real_agent(*args, **kwargs)

    monkeypatch.setattr(agent_module, "Agent", construct_agent)
    real_factory = runtime_module.make_engine_from_env

    def commerce_factory(*args, **kwargs):
        engine = real_factory(*args, **kwargs)
        observed["commerce"].append(engine)
        return engine

    monkeypatch.setattr(runtime_module, "make_engine_from_env", commerce_factory)
    real_build = judge.build_agent

    def build_existing(settings):
        observed["settings"].append(settings)
        agent = real_build(settings)
        observed["policies"].append(agent._registration_policy)
        return agent

    monkeypatch.setattr(judge, "build_agent", build_existing)

    def forbidden_gateway(*args, **kwargs):
        raise AssertionError("Judging launcher attempted an external payment gateway")

    monkeypatch.setattr(runtime_module, "RazorpayGateway", forbidden_gateway)
    return observed


def test_judge_preserves_registered_identity_and_forces_simulation(tmp_path, monkeypatch, construction):
    monkeypatch.chdir(tmp_path)
    seed = "private-test-seed-for-judge-construction-only-123456789"
    hostile = {
        "QUICKFIX_ENABLED": "false", "QUICKFIX_GATEWAY": "razorpay_test",
        "QUICKFIX_PHARMACY_MODE": "connected", "QUICKFIX_MAIL_MODE": "connected",
        "QUICKFIX_AUTO_DEMO": "false", "RAZORPAY_KEY_ID": "rzp_live_must_never_be_used",
        "RAZORPAY_KEY_SECRET": "private-test-secret", "MEDGUARDIAN_AGENT_ADDRESS": "external-peer",
        "MAIL_AGENT_ADDRESS": "external-mail-peer",
    }
    for key, value in hostile.items():
        monkeypatch.setenv(key, value)
    env_file = tmp_path / ".env"
    env_file.write_text("# A private configuration must not be edited.\n", encoding="utf-8")
    original_contents = env_file.read_bytes()

    async def scenario():
        bundle = judge.build_judge(Settings(seed=seed, mode="shopify", asi_key="private-llm-key", port=8765), tmp_path / "judging")
        addresses = (bundle.coordinator.address, bundle.pharmacy_agent.address, bundle.mail_agent.address)
        try:
            await asyncio.sleep(0)  # Only our publication stub runs; no live lifecycle.
            assert bundle.coordinator.address == Identity.from_seed(seed, 0).address
            assert len(set(addresses)) == 3
            passed = construction["settings"][0]
            assert passed.seed == seed
            assert passed.mode == "demo"
            assert passed.asi_key == ""
            assert passed.port == 8765
            engine = construction["commerce"][0]
            assert engine.gateway.mode == "simulated"
            assert engine.pharmacy_mode == "simulated"
            assert engine.mail_mode == "captured"
            assert engine.auto_demo is True
            assert engine.med_agent == bundle.pharmacy_agent.address
            assert engine.mail_agent == bundle.mail_agent.address
            assert bundle.pharmacy.auto_seed is True
            assert bundle.coordinator.mailbox_client is not None
            assert bundle.coordinator._registration_policy is construction["policies"][0]
            assert isinstance(bundle.bureau._registration_policy, judge.NoBatchRegistration)
            assert bundle.bureau._port == 8765
            assert set(construction["manifest_agents"]) == {bundle.coordinator.address}
            assert ChatMessage.build_schema_digest(ChatMessage) in bundle.coordinator._models
            assert PeerResponse.build_schema_digest(PeerResponse) in bundle.coordinator._models
            for peer in (bundle.pharmacy_agent, bundle.mail_agent):
                assert peer._registration_policy is None
                assert peer._network == "testnet"
                assert peer._endpoints == []
                assert peer.mailbox_client is None
                assert peer._report_events is False
                assert peer._mark_inactive_on_shutdown is False
                assert peer._enable_agent_inspector is False
                assert PeerRequest.build_schema_digest(PeerRequest) in peer._models
                assert peer._message_queue_task is None
                await peer._update_agent_status(active=True)
            assert bundle.coordinator._message_queue_task is None
            assert env_file.read_bytes() == original_contents
            assert {key: os.environ.get(key) for key in hostile} == hostile
        finally:
            await bundle.close_unstarted()
        assert all(not dispatcher.contains(address) for address in addresses)
    asyncio.run(scenario())


def test_peer_identities_are_stable_private_and_role_separated(tmp_path, monkeypatch, construction):
    monkeypatch.chdir(tmp_path)
    seed = "private-stable-test-seed-for-judge-only-987654321"

    async def addresses(directory, coordinator_seed):
        bundle = judge.build_judge(Settings(seed=coordinator_seed), directory)
        try:
            await asyncio.sleep(0)
            return bundle.coordinator.address, bundle.pharmacy_agent.address, bundle.mail_agent.address
        finally:
            await bundle.close_unstarted()

    async def scenario():
        first = await addresses(tmp_path / "first", seed)
        repeated = await addresses(tmp_path / "second", seed)
        another = await addresses(tmp_path / "third", seed + "-another")
        assert first == repeated
        assert set(first).isdisjoint(another)
        assert judge._peer_seed(seed, "pharmacy") != judge._peer_seed(seed, "mail")
        assert seed not in judge._peer_seed(seed, "pharmacy")
    asyncio.run(scenario())


def test_judge_refuses_missing_existing_seed_before_creating_data(tmp_path):
    with pytest.raises(ValueError, match="existing private AGENT_SEED"):
        judge.build_judge(Settings(seed=""), tmp_path / "must-not-exist")
    assert not (tmp_path / "must-not-exist").exists()


def test_failed_bridge_construction_restores_environment_and_removes_local_peers(tmp_path, monkeypatch, construction):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("QUICKFIX_GATEWAY", "razorpay_test")
    seed = "private-failure-path-seed-used-for-unit-test-only"

    def failed_bridge(settings):
        assert os.environ["QUICKFIX_GATEWAY"] == "simulated"
        raise RuntimeError("Synthetic bridge construction failure")

    monkeypatch.setattr(judge, "build_agent", failed_bridge)

    async def scenario():
        with pytest.raises(RuntimeError, match="Synthetic bridge"):
            judge.build_judge(Settings(seed=seed), tmp_path / "failed")
        for role in ("pharmacy", "mail"):
            address = Identity.from_seed(judge._peer_seed(seed, role), 0).address
            assert not dispatcher.contains(address)
        assert os.environ["QUICKFIX_GATEWAY"] == "razorpay_test"
        # Closed service connections must not retain file locks after failure.
        for filename in ("pharmacy.sqlite3", "mail.sqlite3"):
            path = tmp_path / "failed" / filename
            path.rename(path.with_suffix(".closed"))
    asyncio.run(scenario())
