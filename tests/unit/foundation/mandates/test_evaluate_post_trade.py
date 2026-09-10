"""L4_compliance_and_regulatory_v1.0.md#9 CM-11 -- `evaluate_post_trade.py`
pure evaluation unit tests.

task-2509 DoD mapping: (a) CM-9(`wash_trade`/`short_sale`)·CM-10
(`market_abuse`) are invoked, not reimplemented -- these tests exercise the
real rule bodies through `evaluate_tenant_day`, not a stand-in. (d) an
exception during evaluation (here, `market_abuse.detect` itself raising)
fails closed into a blocking violation, not a silent empty result.
"""

from __future__ import annotations

import time
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
        "tenant_id": _TENANT,
        "instrument": "AAA",
        "side": "SELL",
        "price": Decimal("100"),
    }
    fill = {
        "tenant_id": _TENANT,
        "instrument": "AAA",
        "side": "BUY",
        "order_qty": Decimal("5"),
        "position_qty": Decimal("0"),
        "borrow_available_qty": Decimal("0"),
        "order_price": Decimal("100"),
        "open_orders": [open_order],
    }
    blocking, warnings = evaluate_tenant_day(
        fill_snapshots=[fill], market_abuse_window=_empty_window(), rule_params={}, now=_NOW
    )
    assert [v.rule_code for v in blocking] == [WASH_TRADE_RULE_ID]
    assert warnings == []


def test_naked_short_sale_blocks() -> None:
    fill = {
        "tenant_id": _TENANT,
        "instrument": "AAA",
        "side": "SELL",
        "order_qty": Decimal("10"),
        "position_qty": Decimal("2"),
        "borrow_available_qty": Decimal("0"),
        "order_price": Decimal("100"),
        "open_orders": [],
    }
    blocking, warnings = evaluate_tenant_day(
        fill_snapshots=[fill], market_abuse_window=_empty_window(), rule_params={}, now=_NOW
    )
    assert [v.rule_code for v in blocking] == [SHORT_SALE_RULE_ID]
    assert warnings == []


def test_allowed_fill_produces_no_violation() -> None:
    fill = {
        "tenant_id": _TENANT,
        "instrument": "AAA",
        "side": "BUY",
        "order_qty": Decimal("5"),
        "position_qty": Decimal("0"),
        "borrow_available_qty": Decimal("0"),
        "order_price": Decimal("100"),
        "open_orders": [],
    }
    blocking, warnings = evaluate_tenant_day(
        fill_snapshots=[fill], market_abuse_window=_empty_window(), rule_params={}, now=_NOW
    )
    assert blocking == []
    assert warnings == []


def test_repeated_violation_across_fills_dedupes_to_one() -> None:
    open_order = {
        "tenant_id": _TENANT,
        "instrument": "AAA",
        "side": "SELL",
        "price": Decimal("100"),
    }
    fill = {
        "tenant_id": _TENANT,
        "instrument": "AAA",
        "side": "BUY",
        "order_qty": Decimal("1"),
        "position_qty": Decimal("0"),
        "borrow_available_qty": Decimal("0"),
        "order_price": Decimal("100"),
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
            "fill_id": "f1",
            "tenant_id": str(_TENANT),
            "instrument_id": "AAA",
            "owner_id": owner,
            "side": "BUY",
            "qty": Decimal("10"),
            "executed_at": _NOW,
        },
        {
            "fill_id": "f2",
            "tenant_id": str(_TENANT),
            "instrument_id": "AAA",
            "owner_id": owner,
            "side": "SELL",
            "qty": Decimal("10"),
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


def test_evaluate_tenant_day_meets_latency_and_throughput_budget() -> None:
    """수치 성능/처리량 -- 이 배치는 스케줄러(`background_loops.py`,
    3600s 주기)에서 돌아가므로 evaluate_tenant_day 자체가 병목이 되면 다음
    tick을 잠식한다. market_abuse의 wash-trade 페어체크가 O(n^2)이라(DEEPEN
    task-2864가 CM-10에서 확인한 것과 같은 특성) 하루치 한 tenant 체결량
    규모(500건)에서 상한(3.0s)과 하한 처리량(150 fills/s)을 수치로 고정한다."""
    fill_count = 500
    fill_snapshots: list[dict[str, object]] = []
    abuse_fills: list[dict[str, object]] = []
    for i in range(fill_count):
        instrument = f"SYM{i % 50}"
        fill_snapshots.append(
            {
                "tenant_id": _TENANT,
                "instrument": instrument,
                "side": "BUY",
                "order_qty": Decimal("1"),
                "position_qty": Decimal("100"),
                "borrow_available_qty": Decimal("0"),
                "order_price": Decimal("100"),
                "open_orders": [],
            }
        )
        abuse_fills.append(
            {
                "fill_id": f"f{i}",
                "tenant_id": str(_TENANT),
                "instrument_id": instrument,
                "owner_id": str(_TENANT),
                "side": "BUY",
                "qty": Decimal("1"),
                "executed_at": _NOW + timedelta(seconds=i),
            }
        )
    window = {"fills": abuse_fills, "orders": [], "market_close_at": _CLOSE}

    start = time.perf_counter()
    blocking, _warnings = evaluate_tenant_day(
        fill_snapshots=fill_snapshots, market_abuse_window=window, rule_params={}, now=_NOW
    )
    elapsed = time.perf_counter() - start

    assert blocking == []
    assert elapsed < 3.0, f"evaluate_tenant_day({fill_count} fills) took {elapsed:.3f}s"
    throughput = fill_count / elapsed
    assert throughput >= 150, f"throughput {throughput:.0f} fills/s below the 150 floor"


def test_gate_color_regression_deny_blocks_warn_passes_through() -> None:
    """게이트/CI 적색 회귀 -- CM-9 DENY(WASH_TRADE)와 CM-10 WARN
    (market_abuse.wash_trade)이 같은 tenant-day 안에 함께 나타나도 색이
    섞이지 않음을 고정한다: DENY만 blocking(게이트 적색)으로 가고 WARN은
    warnings(녹색 유지)에만 남는다. 이 분리가 깨지면 WARN이 blocking으로
    새어 무고한 주문을 막거나, 반대로 DENY가 warnings로 새어 위반 tenant의
    주문을 통과시킨다."""
    open_order = {
        "tenant_id": _TENANT,
        "instrument": "AAA",
        "side": "SELL",
        "price": Decimal("100"),
    }
    deny_fill = {
        "tenant_id": _TENANT,
        "instrument": "AAA",
        "side": "BUY",
        "order_qty": Decimal("5"),
        "position_qty": Decimal("0"),
        "borrow_available_qty": Decimal("0"),
        "order_price": Decimal("100"),
        "open_orders": [open_order],
    }
    owner = str(uuid4())
    warn_fills = [
        {
            "fill_id": "w1",
            "tenant_id": str(_TENANT),
            "instrument_id": "BBB",
            "owner_id": owner,
            "side": "BUY",
            "qty": Decimal("10"),
            "executed_at": _NOW,
        },
        {
            "fill_id": "w2",
            "tenant_id": str(_TENANT),
            "instrument_id": "BBB",
            "owner_id": owner,
            "side": "SELL",
            "qty": Decimal("10"),
            "executed_at": _NOW + timedelta(seconds=5),
        },
    ]
    window = {"fills": warn_fills, "orders": [], "market_close_at": _CLOSE}

    blocking, warnings = evaluate_tenant_day(
        fill_snapshots=[deny_fill], market_abuse_window=window, rule_params={}, now=_NOW
    )
    assert [v.rule_code for v in blocking] == [WASH_TRADE_RULE_ID]
    assert [v.rule_code for v in warnings] == [market_abuse.PATTERN_WASH_TRADE]
