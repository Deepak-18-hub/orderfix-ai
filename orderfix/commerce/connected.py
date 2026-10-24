"""Existing registered coordinator, with real merchant access required.

This launcher never starts local pharmacy/mail simulators. Before authorized
merchant access is configured it answers with a connection-required notice.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, replace
import logging
import os
from pathlib import Path
import sys

from uagents import Agent

from orderfix.agent import build_agent
from orderfix.config import Settings


@contextmanager
def _temporary_environment(values):
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@dataclass
class ConnectedRuntime:
    coordinator: Agent
    data_dir: Path

    def run(self):
        # Public ingress is the registered mailbox, not the laptop's LAN.
        import uagents.asgi as asgi
        previous = asgi.HOST
        asgi.HOST = "127.0.0.1"
        try:
            self.coordinator.run()
        finally:
            asgi.HOST = previous


def build_connected(settings: Settings | None = None,
                    data_dir: str | Path = "data/quickfix-connected") -> ConnectedRuntime:
    """Use the existing seed and separate storage; never modify private .env.

    Like the ordinary Agent constructor, construction may schedule the existing
    coordinator's protocol publication. It does not start its mailbox lifecycle.
    """
    settings = settings or Settings.load()
    if not isinstance(settings.seed, str) or len(settings.seed) < 32:
        raise ValueError("The existing private AGENT_SEED is required; do not create a new identity.")
    root = Path(data_dir).resolve()
    # Root's real-only agent routing avoids creating a legacy provider at all.
    # Keep a separate legacy path as a second boundary if that bridge evolves.
    connected_settings = replace(settings, database=str(root / "legacy-disabled.sqlite3"), asi_key="")
    gateway = os.environ.get("QUICKFIX_GATEWAY", "unconfigured")
    if gateway not in {"unconfigured", "razorpay_live"}:
        gateway = "unconfigured"
    overrides = {
        "QUICKFIX_ENABLED": "true", "QUICKFIX_REQUIRE_REAL": "true",
        "QUICKFIX_DB": str(root / "commerce.sqlite3"), "QUICKFIX_GATEWAY": gateway,
        "QUICKFIX_PHARMACY_MODE": "connected", "QUICKFIX_MAIL_MODE": "connected",
        "QUICKFIX_AUTO_DEMO": "false",
    }
    with _temporary_environment(overrides):
        coordinator = build_agent(connected_settings)
    return ConnectedRuntime(coordinator, root)


def main(argv: list[str] | None = None) -> int:
    if os.name == "nt" and not sys.flags.utf8_mode and argv is None:
        os.execv(sys.executable, [sys.executable, "-X", "utf8", "-m", "orderfix.commerce.connected", *sys.argv[1:]])
    parser = argparse.ArgumentParser(description="Run the registered agent with direct merchant integration required")
    parser.add_argument("--data-dir", default="data/quickfix-connected", help="Separate persistent real-order data directory")
    parser.add_argument("--port", type=int, default=None, help="Local loopback health-server port")
    args = parser.parse_args(argv)
    try:
        settings = Settings.load()
        if args.port is not None:
            if not 1 <= args.port <= 65535:
                raise ValueError("Port must be between 1 and 65535.")
            settings = replace(settings, port=args.port)
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        runtime = build_connected(settings, args.data_dir)
        print("Direct merchant mode: no sample orders or simulated payments.")
        print("Incomplete merchant access returns a connection-required message.")
        print("Live refunds remain disabled unless explicitly enabled in merchant configuration.")
        print("Coordinator address: " + runtime.coordinator.address)
        runtime.run()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("Connected launcher could not start. Check the existing seed, merchant configuration, writable data directory and local port.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
