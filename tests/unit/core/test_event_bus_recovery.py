import pytest

from src.core.event_bus.recovery import recover_pending_orders


async def test_recover_pending_orders_republishes_each_and_counts():
    pending = [{"order_id": "1"}, {"order_id": "2"}]
    republished = []
    recorded = []

    async def fetch_pending_orders():
        return pending

    async def get_order_status(order):
        return {**order, "status": "FILLED"}

    async def republish_order_event(order):
        republished.append(order)

    async def record_recovery(count):
        recorded.append(count)

    result = await recover_pending_orders(
        fetch_pending_orders=fetch_pending_orders,
        get_order_status=get_order_status,
        republish_order_event=republish_order_event,
        record_recovery=record_recovery,
    )

    assert result == 2
    assert republished == [
        {"order_id": "1", "status": "FILLED"},
        {"order_id": "2", "status": "FILLED"},
    ]
    assert recorded == [2]


async def test_recover_pending_orders_skips_failed_status_check():
    async def fetch_pending_orders():
        return [{"order_id": "1"}, {"order_id": "2"}]

    async def get_order_status(order):
        if order["order_id"] == "1":
            raise ConnectionError("exchange unreachable")
        return {**order, "status": "CANCELLED"}

    republished = []

    async def republish_order_event(order):
        republished.append(order)

    result = await recover_pending_orders(
        fetch_pending_orders=fetch_pending_orders,
        get_order_status=get_order_status,
        republish_order_event=republish_order_event,
    )

    assert result == 1
    assert republished == [{"order_id": "2", "status": "CANCELLED"}]


async def test_recover_pending_orders_no_pending_records_zero():
    """Negative: empty pending set must not republish or count anything."""
    recorded = []
    republished = []

    async def fetch_pending_orders():
        return []

    async def get_order_status(order):
        raise AssertionError("get_order_status must not be called when there is nothing pending")

    async def republish_order_event(order):
        republished.append(order)

    async def record_recovery(count):
        recorded.append(count)

    result = await recover_pending_orders(
        fetch_pending_orders=fetch_pending_orders,
        get_order_status=get_order_status,
        republish_order_event=republish_order_event,
        record_recovery=record_recovery,
    )

    assert result == 0
    assert republished == []
    assert recorded == [0]


async def test_recover_pending_orders_all_status_checks_fail_records_zero():
    """Negative: every status recheck failing must still report a clean zero, not crash."""
    recorded = []
    republished = []

    async def fetch_pending_orders():
        return [{"order_id": "1"}, {"order_id": "2"}]

    async def get_order_status(order):
        raise ConnectionError("exchange unreachable")

    async def republish_order_event(order):
        republished.append(order)

    async def record_recovery(count):
        recorded.append(count)

    result = await recover_pending_orders(
        fetch_pending_orders=fetch_pending_orders,
        get_order_status=get_order_status,
        republish_order_event=republish_order_event,
        record_recovery=record_recovery,
    )

    assert result == 0
    assert republished == []
    assert recorded == [0]


async def test_recover_pending_orders_without_record_recovery_callback():
    """Negative: record_recovery is optional and must not be required to run."""

    async def fetch_pending_orders():
        return [{"order_id": "1"}]

    async def get_order_status(order):
        return {**order, "status": "FILLED"}

    async def republish_order_event(order):
        pass

    result = await recover_pending_orders(
        fetch_pending_orders=fetch_pending_orders,
        get_order_status=get_order_status,
        republish_order_event=republish_order_event,
    )

    assert result == 1


async def test_recover_pending_orders_fetch_failure_propagates():
    """Failure injection: fetch_pending_orders raising must fail closed, not be swallowed."""

    async def fetch_pending_orders():
        raise ConnectionError("database unreachable")

    async def get_order_status(order):
        raise AssertionError("must not be reached when fetch fails")

    async def republish_order_event(order):
        raise AssertionError("must not be reached when fetch fails")

    with pytest.raises(ConnectionError, match="database unreachable"):
        await recover_pending_orders(
            fetch_pending_orders=fetch_pending_orders,
            get_order_status=get_order_status,
            republish_order_event=republish_order_event,
        )


async def test_recover_pending_orders_republish_failure_propagates():
    """Failure injection: republish failure must not be swallowed like a status-check failure."""
    republished = []

    async def fetch_pending_orders():
        return [{"order_id": "1"}]

    async def get_order_status(order):
        return {**order, "status": "FILLED"}

    async def republish_order_event(order):
        republished.append(order)
        raise RuntimeError("event bus unavailable")

    with pytest.raises(RuntimeError, match="event bus unavailable"):
        await recover_pending_orders(
            fetch_pending_orders=fetch_pending_orders,
            get_order_status=get_order_status,
            republish_order_event=republish_order_event,
        )

    assert republished == [{"order_id": "1", "status": "FILLED"}]
