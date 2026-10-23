"""Exercise the pinned SDK lifecycle without network calls or a live agent."""
import asyncio
from types import SimpleNamespace

from orderfix import agent as agent_module
from orderfix.config import Settings


def test_mailbox_bootstrap_does_not_exit_and_inspector_remains_disabled(monkeypatch):
    async def scenario():
        # Use the real pinned Agent implementation, but no store or profile IO.
        real_agent = agent_module.Agent
        engine = SimpleNamespace(provider=SimpleNamespace(close=lambda: None),
                                 storage=SimpleNamespace(close=lambda: None))
        monkeypatch.setattr(agent_module, "make_engine", lambda settings: engine)
        def construct(*args, **kwargs):
            kwargs["readme_path"] = None
            return real_agent(*args, **kwargs)
        monkeypatch.setattr(agent_module, "Agent", construct)
        async def no_publish(*args, **kwargs):
            pass
        monkeypatch.setattr(real_agent, "publish_manifest", no_publish)
        agent = agent_module.build_agent(Settings(seed="lifecycle-unit-test-seed-never-used-live-123456789"))
        assert agent._enable_agent_inspector is False
        assert set(agent._rest_handlers) == {("GET", "/health")}
        assert (await agent._rest_handlers[("GET", "/health")](None)).model_dump() == {"status": "ok"}
        observed = []

        async def bootstrap_mailbox():
            # This intentionally returns immediately, matching MailboxClient.run.
            observed.append("mailbox-bootstrap-returned")

        async def server_lifetime():
            observed.append("server-started")
            await asyncio.sleep(0.01)
            assert "mailbox-bootstrap-returned" in observed
            observed.append("server-remained-alive")

        async def shutdown(*args):
            observed.append("shutdown")

        monkeypatch.setattr(agent, "setup", lambda: None)
        monkeypatch.setattr(agent._server, "serve", server_lifetime)
        monkeypatch.setattr(agent._mailbox_client, "run", bootstrap_mailbox)
        monkeypatch.setattr(agent, "_shutdown", shutdown)
        await agent.run_async()
        assert observed.index("server-remained-alive") < observed.index("shutdown")

    asyncio.run(scenario())
