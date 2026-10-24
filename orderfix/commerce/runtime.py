"""Explicit commerce configuration; reading readiness never contacts providers."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
from typing import Mapping

from dotenv import load_dotenv
from uagents_core.identity import is_valid_address

from .payments import PaymentRejected, PaymentUnavailable, RazorpayGateway, SimulatedGateway
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
    live_refunds_enabled: bool = False
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
        if gateway not in {"unconfigured", "simulated", "razorpay_test", "razorpay_live"}:
            raise CommerceConfigurationError("QUICKFIX_GATEWAY must be unconfigured, simulated, razorpay_test or razorpay_live.")
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
        raw_live = environ.get("QUICKFIX_ENABLE_LIVE_REFUNDS", "false").lower()
        if raw_live not in {"true", "false"}:
            raise CommerceConfigurationError("QUICKFIX_ENABLE_LIVE_REFUNDS must be true or false.")
        if raw_live == "true" and gateway != "razorpay_live":
            raise CommerceConfigurationError("Live refunds require the explicitly configured razorpay_live gateway.")
        if gateway in {"razorpay_live", "unconfigured"} and (pharmacy != "connected" or mail != "connected"):
            raise CommerceConfigurationError("Real-order mode requires connected pharmacy and mail services.")
        database = environ.get("QUICKFIX_DB", "data/quickfix.sqlite3")
        merchant = environ.get("QUICKFIX_MERCHANT_REF", "" if gateway in {"razorpay_live", "unconfigured"} else "demo-pharmacy")
        if not database or database == ":memory:":
            raise CommerceConfigurationError("QUICKFIX_DB must name a persistent SQLite file.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", merchant):
            raise CommerceConfigurationError("QUICKFIX_MERCHANT_REF must be an opaque merchant reference.")
        if gateway in {"razorpay_live", "unconfigured"} and re.match(r"(?:demo|sim|simulated|test)(?:[-_]|$)", merchant, re.I):
            raise CommerceConfigurationError("Real-order mode requires an explicit merchant reference, not a simulation reference.")
        return cls(database=database, gateway=gateway, merchant_ref=merchant,
                   med_agent=environ.get("MEDGUARDIAN_AGENT_ADDRESS", ""),
                   mail_agent=environ.get("MAIL_AGENT_ADDRESS", ""), pharmacy_mode=pharmacy,
                   mail_mode=mail, auto_demo=raw_auto == "true", live_refunds_enabled=raw_live == "true",
                   key_id=environ.get("RAZORPAY_KEY_ID", ""),
                   key_secret=environ.get("RAZORPAY_KEY_SECRET", ""))

    def validate(self) -> None:
        for value, label in ((self.med_agent, "MEDGUARDIAN_AGENT_ADDRESS"),
                             (self.mail_agent, "MAIL_AGENT_ADDRESS")):
            if not value.startswith("agent1") or not is_valid_address(value):
                raise CommerceConfigurationError(f"{label} must contain the configured peer agent address.")
        if self.med_agent == self.mail_agent:
            raise CommerceConfigurationError("Pharmacy and mail peers must have distinct agent addresses.")
        if self.gateway in {"razorpay_test", "razorpay_live"}:
            prefix = "rzp_live_" if self.gateway == "razorpay_live" else "rzp_test_"
            if not re.fullmatch(prefix + r"[A-Za-z0-9]+", self.key_id):
                raise CommerceConfigurationError("RAZORPAY_KEY_ID must match the configured gateway mode.")
            if not self.key_secret or any(c.isspace() for c in self.key_secret):
                raise CommerceConfigurationError("RAZORPAY_KEY_SECRET must contain the private merchant secret.")


class UnconfiguredGateway:
    """A read-only order connection must never create fictional payment data."""

    mode = "unconfigured"
    live_refunds_enabled = False
    account_fingerprint = "unconfigured"

    def __init__(self, merchant_ref: str):
        self.merchant_ref = merchant_ref

    async def get_payment(self, payment_id: str):
        raise PaymentUnavailable("Merchant payment access is not configured.")

    async def find_refund(self, payment_id: str, operation_id: str):
        raise PaymentUnavailable("Merchant payment access is not configured.")

    async def create_refund(self, payment_id: str, amount: int, currency: str, operation_id: str):
        raise PaymentRejected("Refunds are unavailable until merchant payment access is configured.")

    def close(self):
        pass


class WaitingCommerce:
    """No storage, external calls, synthetic customers or transactional actions."""

    connected_waiting = True

    def handles(self, sender: str, session: str, text: str) -> bool:
        return True

    async def handle(self, sender: str, session_id: str, message_id: str, text: str) -> dict:
        from .ui import classify_text

        return {"kind": "notice", "code": "connection_required",
                "gateway_mode": "unconfigured", "pharmacy_mode": "connected", "mail_mode": "connected",
                "language": classify_text(text).get("language") or "en"}


def status_from_env(environ: Mapping[str, str] | None = None) -> dict[str, bool]:
    """Booleans only: safe for readiness output, never a credential dump."""
    try:
        settings = CommerceSettings.load(environ)
    except CommerceConfigurationError:
        return {"configuration_valid": False, "ready": False}
    status = {"configuration_valid": True,
              "gateway_simulated": settings.gateway == "simulated",
              "gateway_unconfigured": settings.gateway == "unconfigured",
              "gateway_razorpay_test": settings.gateway == "razorpay_test",
              "gateway_razorpay_live": settings.gateway == "razorpay_live",
              "live_refunds_enabled": settings.live_refunds_enabled,
              "pharmacy_simulated": settings.pharmacy_mode == "simulated",
              "mail_captured": settings.mail_mode == "captured",
              "auto_demo_enabled": settings.auto_demo,
              "pharmacy_address_configured": bool(settings.med_agent),
              "mail_address_configured": bool(settings.mail_agent),
              "razorpay_test_key_configured": bool(re.fullmatch(r"rzp_test_[A-Za-z0-9]+", settings.key_id)),
              "razorpay_live_key_configured": bool(re.fullmatch(r"rzp_live_[A-Za-z0-9]+", settings.key_id)),
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
    if settings.gateway == "unconfigured":
        gateway = UnconfiguredGateway(settings.merchant_ref)
    elif settings.gateway == "simulated":
        gateway = SimulatedGateway(settings.database, settings.merchant_ref)
    else:
        gateway = RazorpayGateway(settings.key_id, settings.key_secret, settings.merchant_ref,
                                  mode=settings.gateway, allow_live_refunds=settings.live_refunds_enabled)
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


def make_connected_engine(environ: Mapping[str, str] | None = None):
    """Return only an authorized real-data engine or a non-transactional notice.

    Incomplete setup is expected while merchant access is pending. No fallback
    to demo identities, fixtures or simulation gateways is permitted here.
    """
    if environ is None:
        load_dotenv()
        environ = os.environ
    try:
        settings = CommerceSettings.load(environ)
        if (settings.gateway not in {"unconfigured", "razorpay_live"}
                or settings.pharmacy_mode != "connected" or settings.mail_mode != "connected"
                or settings.auto_demo):
            raise CommerceConfigurationError("The connected launcher accepts real merchant configuration only.")
        settings.validate()
        return make_engine_from_env(environ)
    except CommerceConfigurationError:
        return WaitingCommerce()
