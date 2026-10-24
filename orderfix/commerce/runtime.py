"""Explicit commerce configuration; reading readiness never contacts providers."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
from typing import Mapping

from dotenv import load_dotenv
from uagents_core.identity import is_valid_address

from .payments import RazorpayGateway, SimulatedGateway
from .store import CommerceStore


class CommerceConfigurationError(ValueError):
    """A sanitized, actionable configuration error."""


@dataclass(frozen=True)
class CommerceSettings:
    database: str = "data/quickfix.sqlite3"
    gateway: str = "simulated"
    merchant_ref: str = "demo-pharmacy"
    med_agent: str = ""
    mail_agent: str = ""
    pharmacy_mode: str = "simulated"
    mail_mode: str = "captured"
    auto_demo: bool = False
    key_id: str = field(default="", repr=False)
    key_secret: str = field(default="", repr=False)

    @classmethod
    def load(cls, environ: Mapping[str, str] | None = None) -> "CommerceSettings":
        if environ is None:
            load_dotenv()
            environ = os.environ
        gateway = environ.get("QUICKFIX_GATEWAY", "simulated")
        pharmacy = environ.get("QUICKFIX_PHARMACY_MODE", "simulated")
        mail = environ.get("QUICKFIX_MAIL_MODE", "captured")
        if gateway not in {"simulated", "razorpay_test"}:
            raise CommerceConfigurationError("QUICKFIX_GATEWAY must be simulated or razorpay_test.")
        if pharmacy not in {"simulated", "connected"}:
            raise CommerceConfigurationError("QUICKFIX_PHARMACY_MODE must be simulated or connected.")
        if mail not in {"captured", "connected"}:
            raise CommerceConfigurationError("QUICKFIX_MAIL_MODE must be captured or connected.")
        fully_simulated = gateway == "simulated" and pharmacy == "simulated" and mail == "captured"
        raw_auto = environ.get("QUICKFIX_AUTO_DEMO", "false").lower()
        if raw_auto not in {"true", "false"}:
            raise CommerceConfigurationError("QUICKFIX_AUTO_DEMO must be true or false.")
        if raw_auto == "true" and not fully_simulated:
            raise CommerceConfigurationError("QUICKFIX_AUTO_DEMO must be false when any service is connected or uses Razorpay.")
        database = environ.get("QUICKFIX_DB", "data/quickfix.sqlite3")
        merchant = environ.get("QUICKFIX_MERCHANT_REF", "demo-pharmacy")
        if not database or database == ":memory:":
            raise CommerceConfigurationError("QUICKFIX_DB must name a persistent SQLite file.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", merchant):
            raise CommerceConfigurationError("QUICKFIX_MERCHANT_REF must be an opaque merchant reference.")
        return cls(database=database, gateway=gateway, merchant_ref=merchant,
                   med_agent=environ.get("MEDGUARDIAN_AGENT_ADDRESS", ""),
                   mail_agent=environ.get("MAIL_AGENT_ADDRESS", ""), pharmacy_mode=pharmacy,
                   mail_mode=mail, auto_demo=raw_auto == "true",
                   key_id=environ.get("RAZORPAY_KEY_ID", ""),
                   key_secret=environ.get("RAZORPAY_KEY_SECRET", ""))

    def validate(self) -> None:
        for value, label in ((self.med_agent, "MEDGUARDIAN_AGENT_ADDRESS"),
                             (self.mail_agent, "MAIL_AGENT_ADDRESS")):
            if not value.startswith("agent1") or not is_valid_address(value):
                raise CommerceConfigurationError(f"{label} must contain the configured peer agent address.")
        if self.med_agent == self.mail_agent:
            raise CommerceConfigurationError("Pharmacy and mail peers must have distinct agent addresses.")
        if self.gateway == "razorpay_test":
            if not re.fullmatch(r"rzp_test_[A-Za-z0-9]+", self.key_id):
                raise CommerceConfigurationError("RAZORPAY_KEY_ID must be a test key; live mode is disabled.")
            if not self.key_secret or any(c.isspace() for c in self.key_secret):
                raise CommerceConfigurationError("RAZORPAY_KEY_SECRET must contain the private test secret.")


def status_from_env(environ: Mapping[str, str] | None = None) -> dict[str, bool]:
    """Booleans only: safe for readiness output, never a credential dump."""
    try:
        settings = CommerceSettings.load(environ)
    except CommerceConfigurationError:
        return {"configuration_valid": False, "ready": False}
    status = {"configuration_valid": True,
              "gateway_simulated": settings.gateway == "simulated",
              "gateway_razorpay_test": settings.gateway == "razorpay_test",
              "pharmacy_simulated": settings.pharmacy_mode == "simulated",
              "mail_captured": settings.mail_mode == "captured",
              "auto_demo_enabled": settings.auto_demo,
              "pharmacy_address_configured": bool(settings.med_agent),
              "mail_address_configured": bool(settings.mail_agent),
              "razorpay_test_key_configured": bool(re.fullmatch(r"rzp_test_[A-Za-z0-9]+", settings.key_id)),
              "razorpay_secret_configured": bool(settings.key_secret)}
    try:
        settings.validate()
        status["ready"] = True
    except CommerceConfigurationError:
        status["ready"] = False
    return status


def make_engine_from_env(environ: Mapping[str, str] | None = None):
    """Create the durable engine; validation precedes any database creation."""
    from .engine import CommerceEngine

    settings = CommerceSettings.load(environ)
    settings.validate()
    gateway = (SimulatedGateway(settings.database, settings.merchant_ref)
               if settings.gateway == "simulated" else
               RazorpayGateway(settings.key_id, settings.key_secret, settings.merchant_ref))
    store = None
    try:
        store = CommerceStore(settings.database)
        return CommerceEngine(store, gateway, settings.med_agent, settings.mail_agent,
                              pharmacy_mode=settings.pharmacy_mode, mail_mode=settings.mail_mode,
                              auto_demo=settings.auto_demo)
    except Exception:
        gateway.close()
        if store is not None:
            store.close()
        raise
