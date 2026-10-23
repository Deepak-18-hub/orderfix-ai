from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

Language = Literal["en", "hi", "kn"]
Reason = Literal["damaged", "wrong_item", "size", "changed_mind", "other"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LineItem(Model):
    id: str
    title: str
    quantity: int = Field(ge=1)
    returnable_quantity: int = Field(ge=0)
    returnable_id: str | None = None
    excluded: bool = False


class ReturnRecord(Model):
    id: str
    number: str
    order_id: str
    line_id: str
    quantity: int = Field(ge=1)
    reason: Reason
    status: str
    created_at: datetime
    operation_id: str
    mode: Literal["demo", "shopify"]


class Order(Model):
    id: str
    number: str
    customer_ref: str
    fulfillment_status: str
    fulfilled_at: datetime | None = None
    currency: str = "INR"
    total: str
    lines: list[LineItem]
    returns: list[ReturnRecord] = Field(default_factory=list)
    mode: Literal["demo", "shopify"]


class Eligibility(Model):
    eligible: bool
    code: Literal["eligible", "not_fulfilled", "window_expired", "excluded", "quantity_unavailable", "manual_review"]
    window_days: int
    policy_label: str


class Intent(Model):
    action: Literal["help", "lookup", "return", "status", "language", "unknown"] = "unknown"
    order_number: str | None = None
    item_index: int | None = Field(default=None, ge=1, le=100)
    quantity: int | None = Field(default=None, ge=1, le=20)
    reason: Reason | None = None
    language: Language | None = None


class Review(Model):
    token: str
    order_number: str
    order_id: str
    line_id: str
    item_title: str
    quantity: int = Field(ge=1, le=20)
    reason: Reason
    eligibility: Eligibility
    created_at: datetime = Field(default_factory=utcnow)
    mode: Literal["demo", "shopify"]


class Session(Model):
    language: Language = "en"
    customer_ref: str | None = None
    order_number: str | None = None
    item_index: int | None = None
    quantity: int = 1
    reason: Reason | None = None
    pending: Review | None = None
    last_return: ReturnRecord | None = None
    stage: Literal["idle", "item", "reason", "review"] = "idle"


class Reply(Model):
    text: str
    language: Language = "en"
    review: Review | None = None
    return_record: ReturnRecord | None = None


class ProviderError(Exception):
    """Sanitized recoverable tool failure. Never include credentials or full API bodies."""


class AmbiguousAction(ProviderError):
    """An external action may have succeeded. Reconcile; never retry blindly."""


class StoreProvider(Protocol):
    mode: Literal["demo", "shopify"]

    async def get_order(self, number: str, customer_ref: str) -> Order | None: ...

    async def request_return(self, order: Order, line_id: str, quantity: int,
                             reason: Reason, operation_id: str) -> ReturnRecord: ...

    async def find_return(self, order: Order, operation_id: str) -> ReturnRecord | None: ...

    def close(self) -> None: ...
