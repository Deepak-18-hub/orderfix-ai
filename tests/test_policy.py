from datetime import datetime, timedelta, timezone

import pytest

from orderfix.models import LineItem, Order
from orderfix.policy import ReturnPolicy


NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def order():
    return Order(
        id="test", number="#1001", customer_ref="alice", fulfillment_status="FULFILLED",
        fulfilled_at=NOW - timedelta(days=3), total="1999.00", mode="demo",
        lines=[LineItem(id="line-1", title="Headphones", quantity=2, returnable_quantity=2)],
    )


def test_eligible_window_includes_exact_boundary(order):
    policy = ReturnPolicy()
    order.fulfilled_at = NOW - timedelta(days=14)
    result = policy.evaluate(order, "line-1", 2, NOW)
    assert result.eligible
    assert result.code == "eligible"
    assert result.window_days == 14
    assert result.policy_label.startswith("DEMO POLICY:")
    order.fulfilled_at -= timedelta(microseconds=1)
    assert policy.evaluate(order, "line-1", 1, NOW).code == "window_expired"


@pytest.mark.parametrize("date", [None, NOW.replace(tzinfo=None), NOW + timedelta(seconds=1)])
def test_unknown_ambiguous_or_future_fulfillment_never_approved(order, date):
    order.fulfilled_at = date
    result = ReturnPolicy().evaluate(order, "line-1", 1, NOW)
    assert not result.eligible
    assert result.code == "manual_review"


def test_naive_comparison_time_requires_review(order):
    assert ReturnPolicy().evaluate(order, "line-1", 1, NOW.replace(tzinfo=None)).code == "manual_review"


@pytest.mark.parametrize("status", ["UNFULFILLED", "PARTIALLY_FULFILLED", "UNKNOWN"])
def test_unfulfilled_or_unclear_status_is_not_approved(order, status):
    order.fulfillment_status = status
    assert ReturnPolicy().evaluate(order, "line-1", 1, NOW).code == "not_fulfilled"


def test_excluded_item(order):
    order.lines[0].excluded = True
    assert ReturnPolicy().evaluate(order, "line-1", 1, NOW).code == "excluded"


@pytest.mark.parametrize("quantity", [0, -1, 3, True, 1.5, "1"])
def test_invalid_or_overflow_quantity(order, quantity):
    assert ReturnPolicy().evaluate(order, "line-1", quantity, NOW).code == "quantity_unavailable"


def test_unknown_line_and_already_reserved_units(order):
    policy = ReturnPolicy()
    assert policy.evaluate(order, "another-line", 1, NOW).code == "quantity_unavailable"
    order.lines[0].returnable_quantity = 0
    assert policy.evaluate(order, "line-1", 1, NOW).code == "quantity_unavailable"


def test_quantity_cannot_exceed_original_order_even_if_provider_inconsistent(order):
    order.lines[0].returnable_quantity = 20
    assert ReturnPolicy().evaluate(order, "line-1", 3, NOW).code == "quantity_unavailable"


def test_custom_window_and_label(order):
    policy = ReturnPolicy(window_days=2, label="Merchant 2-day policy")
    result = policy.evaluate(order, "line-1", 1, NOW)
    assert result.code == "window_expired"
    assert result.policy_label == "Merchant 2-day policy"


@pytest.mark.parametrize("window", [0, -1, True, 1.5])
def test_invalid_policy_window(window):
    with pytest.raises(ValueError):
        ReturnPolicy(window_days=window)
