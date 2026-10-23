"""Explicit sample-store rules; these are not a retailer's live policy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import Eligibility, Order


class ReturnPolicy:
    def __init__(
        self,
        window_days: int = 14,
        label: str = "DEMO POLICY: returns within 14 days after fulfillment",
    ) -> None:
        if isinstance(window_days, bool) or not isinstance(window_days, int) or window_days < 1:
            raise ValueError("Return window must be a positive number of days.")
        self.window_days = window_days
        self.label = label

    def evaluate(
        self, order: Order, line_id: str, quantity: int, now: datetime | None = None
    ) -> Eligibility:
        def result(code: str) -> Eligibility:
            return Eligibility(
                eligible=code == "eligible", code=code,
                window_days=self.window_days, policy_label=self.label,
            )

        line = next((item for item in order.lines if item.id == line_id), None)
        if line is None or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            return result("quantity_unavailable")
        if line.excluded:
            return result("excluded")
        if order.fulfillment_status.upper() != "FULFILLED":
            return result("not_fulfilled")

        current = now if now is not None else datetime.now(timezone.utc)
        fulfilled = order.fulfilled_at
        # An unknown or timezone-less timestamp cannot establish the return window.
        if (fulfilled is None or fulfilled.utcoffset() is None
                or current.utcoffset() is None or fulfilled > current):
            return result("manual_review")
        if current > fulfilled + timedelta(days=self.window_days):
            return result("window_expired")
        if quantity > min(line.quantity, line.returnable_quantity):
            return result("quantity_unavailable")
        return result("eligible")
