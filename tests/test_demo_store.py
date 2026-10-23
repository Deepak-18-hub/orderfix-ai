import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from orderfix.demo_store import DemoProvider
from orderfix.models import ProviderError
from orderfix.policy import ReturnPolicy


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def provider(tmp_path):
    store = DemoProvider(str(tmp_path / "store.sqlite3"))
    yield store
    store.close()


def test_seeded_scenarios_and_order_normalization(provider):
    normal = run(provider.get_order(" 1001 ", "alice"))
    assert normal.mode == "demo"
    assert normal.number == "#1001"
    assert [line.quantity for line in normal.lines] == [2, 1]
    assert [line.title for line in normal.lines] == ["Wireless headphones", "USB-C cable"]
    assert run(provider.get_order("100000", "alice")) is None
    expected = {"1001": "eligible", "1002": "window_expired", "1003": "not_fulfilled", "1004": "excluded", "1005": "quantity_unavailable"}
    for number, code in expected.items():
        order = run(provider.get_order(number, "alice"))
        assert ReturnPolicy().evaluate(order, order.lines[0].id, 1).code == code
    existing = run(provider.get_order("1005", "alice"))
    assert existing.returns[0].status == "REQUESTED"
    assert existing.returns[0].mode == "demo"


def test_return_is_committed_readable_and_survives_restart(tmp_path):
    path = str(tmp_path / "store.sqlite3")
    first = DemoProvider(path)
    order = run(first.get_order("1001", "alice"))
    record = run(first.request_return(order, order.lines[0].id, 1, "damaged", "operation-1"))
    assert record.status == "REQUESTED"
    assert record.mode == "demo"
    assert record.number.startswith("DEMO-R-")
    first.close()
    second = DemoProvider(path)
    try:
        loaded = run(second.get_order("1001", "alice"))
        assert loaded.fulfilled_at == order.fulfilled_at
        assert loaded.lines[0].returnable_quantity == 1
        assert loaded.lines[1].returnable_quantity == 1
        assert loaded.returns == [record]
        assert run(second.find_return(loaded, "operation-1")) == record
        assert run(second.find_return(loaded, "missing-operation")) is None
    finally:
        second.close()


def test_seed_timestamp_never_refreshes_after_restart(tmp_path, monkeypatch):
    path = str(tmp_path / "persist.sqlite3")
    initial = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("orderfix.demo_store.utcnow", lambda: initial)
    store = DemoProvider(path)
    before = run(store.get_order("1001", "alice"))
    seed_return = run(store.get_order("1005", "alice")).returns[0]
    store.close()
    later = initial + timedelta(days=30)
    monkeypatch.setattr("orderfix.demo_store.utcnow", lambda: later)
    store = DemoProvider(path)
    try:
        after = run(store.get_order("1001", "alice"))
        assert after.fulfilled_at == before.fulfilled_at
        assert ReturnPolicy().evaluate(after, after.lines[0].id, 1, later).code == "window_expired"
        assert run(store.get_order("1005", "alice")).returns == [seed_return]
    finally:
        store.close()


def test_customers_cannot_read_or_spend_each_others_quantities(provider):
    alice = run(provider.get_order("1001", "alice"))
    bob = run(provider.get_order("1001", "bob"))
    assert alice.id != bob.id
    record = run(provider.request_return(alice, alice.lines[0].id, 2, "damaged", "same-operation"))
    assert run(provider.find_return(bob, "same-operation")) is None
    assert run(provider.get_order("1001", "bob")).lines[0].returnable_quantity == 2
    other = run(provider.request_return(bob, bob.lines[0].id, 1, "damaged", "same-operation"))
    assert other.id != record.id
    forged = alice.model_copy(update={"customer_ref": "bob"})
    with pytest.raises(ProviderError, match="does not belong"):
        run(provider.find_return(forged, "same-operation"))
    with pytest.raises(ProviderError, match="does not belong"):
        run(provider.request_return(forged, alice.lines[0].id, 1, "damaged", "forged-operation"))


def test_identical_operation_replay_returns_original_even_when_quantity_exhausted(provider):
    order = run(provider.get_order("1001", "alice"))
    first = run(provider.request_return(order, order.lines[0].id, 2, "damaged", "retry-1"))
    repeat = run(provider.request_return(order, order.lines[0].id, 2, "damaged", "retry-1"))
    assert repeat == first
    saved = run(provider.get_order("1001", "alice"))
    assert len(saved.returns) == 1
    assert saved.lines[0].returnable_quantity == 0
    with pytest.raises(ProviderError, match="quantity_unavailable"):
        run(provider.request_return(order, order.lines[0].id, 1, "damaged", "retry-2"))


def test_operation_cannot_be_reused_with_changed_payload(provider):
    order = run(provider.get_order("1001", "alice"))
    run(provider.request_return(order, order.lines[0].id, 1, "damaged", "key"))
    for line_id, quantity, reason in [(order.lines[0].id, 2, "damaged"), (order.lines[0].id, 1, "other"), (order.lines[1].id, 1, "damaged")]:
        with pytest.raises(ProviderError, match="different return"):
            run(provider.request_return(order, line_id, quantity, reason, "key"))
    another = run(provider.get_order("1005", "alice"))
    with pytest.raises(ProviderError, match="different return"):
        run(provider.request_return(another, another.lines[0].id, 1, "damaged", "key"))
    assert len(run(provider.get_order("1001", "alice")).returns) == 1


def test_provider_rechecks_policy_and_does_not_trust_caller_order(provider):
    for number, code in [("1002", "window_expired"), ("1003", "not_fulfilled"), ("1004", "excluded")]:
        order = run(provider.get_order(number, "alice"))
        order.fulfillment_status = "FULFILLED"
        order.fulfilled_at = datetime.now(timezone.utc) - timedelta(days=1)
        order.lines[0].excluded = False
        with pytest.raises(ProviderError, match=code):
            run(provider.request_return(order, order.lines[0].id, 1, "damaged", f"policy-{number}"))


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5])
def test_invalid_quantity_never_commits(provider, quantity):
    order = run(provider.get_order("1001", "alice"))
    with pytest.raises(ProviderError):
        run(provider.request_return(order, order.lines[0].id, quantity, "damaged", "invalid"))
    assert run(provider.get_order("1001", "alice")).returns == []


def test_unknown_line_unsupported_reason_and_empty_operation_fail(provider):
    order = run(provider.get_order("1001", "alice"))
    for line, reason, operation in [("missing", "damaged", "key"), (order.lines[0].id, "fraudulent", "key"), (order.lines[0].id, "damaged", " ")]:
        with pytest.raises(ProviderError):
            run(provider.request_return(order, line, 1, reason, operation))
    assert run(provider.get_order("1001", "alice")).returns == []


def test_concurrent_requests_cannot_over_allocate(provider):
    order = run(provider.get_order("1001", "alice"))

    def attempt(index):
        try:
            return run(provider.request_return(order, order.lines[0].id, 1, "damaged", f"parallel-{index}"))
        except ProviderError:
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(attempt, range(8)))
    assert len([result for result in results if result]) == 2
    latest = run(provider.get_order("1001", "alice"))
    assert latest.lines[0].returnable_quantity == 0
    assert sum(record.quantity for record in latest.returns) == 2


def test_concurrent_retries_are_idempotent_across_connections(tmp_path):
    path = str(tmp_path / "shared.sqlite3")
    providers = [DemoProvider(path) for _ in range(4)]
    try:
        order = run(providers[0].get_order("1001", "alice"))

        def attempt(index):
            return run(providers[index].request_return(order, order.lines[0].id, 1, "damaged", "same-key"))

        with ThreadPoolExecutor(max_workers=4) as executor:
            records = list(executor.map(attempt, range(4)))
        assert len({record.id for record in records}) == 1
        assert len(run(providers[0].get_order("1001", "alice")).returns) == 1
    finally:
        for store in providers:
            store.close()


def test_empty_customer_and_closed_provider_fail_safely(provider):
    with pytest.raises(ProviderError, match="customer"):
        run(provider.get_order("1001", " "))
    provider.close()
    provider.close()
    with pytest.raises(ProviderError, match="closed"):
        run(provider.get_order("1001", "alice"))
