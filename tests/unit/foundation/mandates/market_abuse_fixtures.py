"""CM-10 market-abuse test input builders."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

_T0 = datetime(2026, 1, 5, 5, 0, 0, tzinfo=UTC)
_CLOSE = datetime(2026, 1, 5, 6, 0, 0, tzinfo=UTC)


def _fill(**kwargs: object) -> dict[str, object]:
    base = {
        "fill_id": "f1",
        "tenant_id": "tenant-a",
        "instrument_id": "005930",
        "owner_id": "owner-1",
        "side": "BUY",
        "qty": Decimal("10"),
        "executed_at": _T0,
    }
    base.update(kwargs)
    return base


def _order(**kwargs: object) -> dict[str, object]:
    base = {
        "order_id": "o1",
        "tenant_id": "tenant-a",
        "instrument_id": "005930",
        "qty": Decimal("10"),
        "submitted_at": _T0,
        "canceled_at": _T0 + timedelta(seconds=1),
    }
    base.update(kwargs)
    return base


def _window(**kwargs: object) -> dict[str, object]:
    base: dict[str, object] = {"fills": [], "orders": [], "market_close_at": _CLOSE}
    base.update(kwargs)
    return base


