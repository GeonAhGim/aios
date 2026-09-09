"""L4_compliance_and_regulatory_v1.0.md#9 CM-11 -- `evaluate_post_trade.py`
pure evaluation unit tests.

task-2509 DoD mapping: (a) CM-9(`wash_trade`/`short_sale`)·CM-10
(`market_abuse`) are invoked, not reimplemented -- these tests exercise the
real rule bodies through `evaluate_tenant_day`, not a stand-in. (d) an
exception during evaluation (here, `market_abuse.detect` itself raising)
fails closed into a blocking violation, not a silent empty result.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.mandates.application.evaluate_post_trade import (
    _violation_reason,
    evaluate_tenant_day,
)
from src.foundation.mandates.domain import market_abuse
from src.foundation.mandates.domain.rules.short_sale import RULE_ID as SHORT_SALE_RULE_ID
from src.foundation.mandates.domain.rules.wash_trade import RULE_ID as WASH_TRADE_RULE_ID

_NOW = datetime(2026, 1, 5, 6, 0, 0, tzinfo=timezone.utc)
_CLOSE = datetime(2026, 1, 5, 7, 0, 0, tzinfo=timezone.utc)
_TENANT = uuid4()


def _empty_window() -> dict[str, object]:
    return {"fills": [], "orders": [], "market_close_at": _CLOSE}


def test_wash_trade_crossing_fill_blocks() -> None:
    """DoD (b)'s canonical example -- a fill crossing the tenant's own still
    open order is a `WASH_TRADE` DENY, reused straight from CM-9."""
    open_order = {
        "tenant_id": _TENANT, "instrument": "AAA", "side": "SELL", "price": Decimal("100"),
    }
    fill = {
        "tenant_id": _TENANT, "instrument": "AAA", "side": "BUY",
        "order_qty": Decimal("5"), "position_qty": Decimal("0"),
        "borrow_available_qty": Decimal("0"), "order_price": Decimal("100"),
        "open_orders": [open_order],
    }
    blocking, warnings = evaluate_tenant_day(
        fill_snapshots=[fill], market_abuse_window=_empty_window(), rule_params={}, now=_NOW
    )
    assert [v.rule_code for v in blocking] == [WASH_TRADE_RULE_ID]
    assert warnings == []


def test_naked_short_sale_blocks() -> None:
    fill = {
        "tenant_id": _TENANT, "instrument": "AAA", "side": "SELL",
        "order_qty": Decimal("10"), "position_qty": Decimal("2"),
        "borrow_available_qty": Decimal("0"), "order_price": Decimal("100"),
        "open_orders": [],
    }
    blocking, warnings = evaluate_tenant_day(
        fill_snapshots=[fill], market_abuse_window=_empty_window(), rule_params={}, now=_NOW
    )
    assert [v.rule_code for v in blocking] == [SHORT_SALE_RULE_ID]
    assert warnings == []


def test_allowed_fill_produces_no_violation() -> None:
    fill = {
        "tenant_id": _TENANT, "instrument": "AAA", "side": "BUY",
        "order_qty": Decimal("5"), "position_qty": Decimal("0"),
        "borrow_available_qty": Decimal("0"), "order_price": Decimal("100"),
        "open_orders": [],
    }
    blocking, warnings = evaluate_tenant_day(
        fill_snapshots=[fill], market_abuse_window=_empty_window(), rule_params={}, now=_NOW
    )
    assert blocking == []
    assert warnings == []


def test_repeated_violation_across_fills_dedupes_to_one() -> None:
    open_order = {
        "tenant_id": _TENANT, "instrument": "AAA", "side": "SELL", "price": Decimal("100"),
    }
    fill = {
        "tenant_id": _TENANT, "instrument": "AAA", "side": "BUY",
        "order_qty": Decimal("1"), "position_qty": Decimal("0"),
        "borrow_available_qty": Decimal("0"), "order_price": Decimal("100"),
        "open_orders": [open_order],
    }
    blocking, _ = evaluate_tenant_day(
        fill_snapshots=[fill, dict(fill)],
        market_abuse_window=_empty_window(),
        rule_params={},
        now=_NOW,
    )
    assert len(blocking) == 1


def test_market_abuse_warn_pattern_does_not_block() -> None:
    """CM-10's real detection patterns are WARN (§3 "WARN은 통과시키되
    기록") -- only `DATA_MISSING` blocks."""
    owner = str(uuid4())
    fills = [
        {
            "fill_id": "f1", "tenant_id": str(_TENANT), "instrument_id": "AAA",
            "owner_id": owner, "side": "BUY", "qty": Decimal("10"), "executed_at": _NOW,
        },
        {
            "fill_id": "f2", "tenant_id": str(_TENANT), "instrument_id": "AAA",
            "owner_id": owner, "side": "SELL", "qty": Decimal("10"),
            "executed_at": _NOW + timedelta(seconds=5),
        },
    ]
    window = {"fills": fills, "orders": [], "market_close_at": _CLOSE}
    blocking, warnings = evaluate_tenant_day(
        fill_snapshots=[], market_abuse_window=window, rule_params={}, now=_NOW
    )
    assert blocking == []
    assert any(v.rule_code == market_abuse.PATTERN_WASH_TRADE for v in warnings)


def test_market_abuse_missing_data_fails_closed() -> None:
    window = {"orders": [], "market_close_at": _CLOSE}  # "fills" 키 자체가 없음
    blocking, _ = evaluate_tenant_day(
        fill_snapshots=[], market_abuse_window=window, rule_params={}, now=_NOW
    )
    assert len(blocking) == 3  # 3 patterns marked DATA_MISSING (I-02)


def test_market_abuse_exception_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """DoD (d) -- an exception `market_abuse.detect` itself raises (not
    caught by its internal `_safe` wrapper) must still block, not vanish."""

    def _raise(*_args: object, **_kwargs: object) -> list[object]:
        raise ValueError("boom")

    monkeypatch.setattr(market_abuse, "detect", _raise)
    blocking, _ = evaluate_tenant_day(
        fill_snapshots=[], market_abuse_window=_empty_window(), rule_params={}, now=_NOW
    )
    assert len(blocking) == 1
    assert blocking[0].rule_code == "market_abuse.evaluation_exception"


def test_violation_reason_encodes_tenant_scoped_key() -> None:
    reason = _violation_reason(WASH_TRADE_RULE_ID, date(2026, 1, 5))
    assert reason == f"COMPLIANCE:{WASH_TRADE_RULE_ID}:2026-01-05"
