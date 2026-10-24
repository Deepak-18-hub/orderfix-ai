from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


def now() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Medicine(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    strength: str = Field(min_length=1, max_length=80)
    form: str = Field(min_length=1, max_length=80)
    quantity: StrictInt = Field(gt=0, le=1000)


class PharmacyOrder(StrictModel):
    order_id: str = Field(min_length=1, max_length=200)
    customer_ref: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    pharmacy_ref: str = Field(min_length=1, max_length=128)
    pharmacy_name: str = Field(min_length=1, max_length=160)
    merchant_ref: str = Field(min_length=1, max_length=128)
    payment_id: str = Field(min_length=1, max_length=160)
    amount_minor: StrictInt = Field(gt=0, le=100000000)
    currency: Literal["INR"]
    order_state: Literal["accepted", "dispatched", "cancelled"]
    version: StrictInt = Field(ge=0)
    customer_email: str = Field(max_length=254)
    pharmacy_email: str = Field(max_length=254)
    items: list[Medicine] = Field(min_length=1, max_length=20)
    data_mode: Literal["simulated", "connected"]

    @field_validator("customer_email", "pharmacy_email")
    @classmethod
    def email_shape(cls, value: str) -> str:
        if not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+", value):
            raise ValueError("A single verified recipient address is required")
        return value


class CommerceSession(StrictModel):
    language: Literal["en", "hi", "kn"] = "en"
    customer_ref: str | None = None
    active_case: str | None = None
    orders: dict[str, PharmacyOrder] = Field(default_factory=dict)
    orders_expires: datetime | None = None
    view: dict = Field(default_factory=lambda: {"kind": "help"})
    route_sender: str = ""
    route_session: str = ""


class CommerceCase(StrictModel):
    id: str
    owner: str
    customer_ref: str
    order: PharmacyOrder
    version: int = 0
    stage: Literal["eligibility", "payment_check", "review", "payment_recheck", "cancelling", "refund", "refund_pending", "done", "rejected", "blocked", "draft_cancelled"] = "eligibility"
    token: str
    expires_at: datetime
    policy_text: str = ""
    amount_minor: int = 0
    approval_ref: str | None = None
    cancellation_key: str
    refund_key: str
    payment_state: str = "unverified"
    refund_state: Literal["not_requested", "submitting", "pending", "processed", "failed", "blocked"] = "not_requested"
    refund_id: str | None = None
    refund_dispatched: bool = False
    binding_fingerprint: str = ""
    customer_notification: str = "not_queued"
    pharmacy_notification: str = "not_queued"
    last_error: str | None = None
    gateway_mode: Literal["simulated", "razorpay_test"] = "simulated"
    pharmacy_mode: Literal["simulated", "connected"] = "simulated"
    mail_mode: Literal["captured", "connected"] = "captured"
    updated_at: datetime = Field(default_factory=now)
