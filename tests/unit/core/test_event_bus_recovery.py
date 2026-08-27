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
