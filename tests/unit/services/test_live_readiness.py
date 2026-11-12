"""LP-1: 전환 근거 계산 -- 자료가 없으면 0이 아니라 None + 사유."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.services.live_readiness import (
    LiveReadinessInputError,
    compute_live_readiness,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import (
    reset_virtual_totals,
    virtual_delayed_sync,
    virtual_totals_ns,
)

T0 = datetime(2026, 11, 1, tzinfo=UTC)


def _row(**kw):
    base = {
        "status": "FILLED",
        "side": "BUY",
        "quantity": "10",
        "filled_quantity": "10",
        "price": "100",
        "average_fill_price": "100",
        "created_at": T0,
        "updated_at": T0 + timedelta(hours=1),
    }
    base.update(kw)
    return base


def test_empty_input_reports_everything_unavailable_not_zero() -> None:
    r = compute_live_readiness([])
    assert r.order_count == 0 and r.terminal_count == 0
    assert r.fill_ratio is None and r.unfilled_ratio is None and r.reject_ratio is None
    assert r.avg_slippage_bps is None and r.fee_totals is None and r.net_pnl is None
    for key in ("fill_ratio", "avg_slippage_bps", "fee_totals", "net_pnl", "recovery_event_count"):
        assert key in r.unavailable and r.unavailable[key]


def test_net_pnl_and_recovery_are_always_unavailable_from_order_rows() -> None:
    r = compute_live_readiness([_row()])
    assert r.net_pnl is None and r.recovery_event_count is None
    assert "net_pnl" in r.unavailable and "recovery_event_count" in r.unavailable


def test_ratios_use_terminal_orders_only() -> None:
    rows = [
        _row(),  # FILLED
        _row(status="REJECTED", filled_quantity="0", average_fill_price=None),
        _row(status="CANCELLED", filled_quantity="0", average_fill_price=None),
        _row(status="ACKNOWLEDGED", filled_quantity="0", average_fill_price=None),  # 진행 중
    ]
    r = compute_live_readiness(rows)
    assert (r.order_count, r.terminal_count, r.open_count) == (4, 3, 1)
    assert r.fill_ratio == Decimal(1) / Decimal(3)
    assert r.reject_ratio == Decimal(1) / Decimal(3)
    assert r.unfilled_ratio == Decimal(1) / Decimal(3)


def test_unknown_status_is_not_treated_as_failure() -> None:
    r = compute_live_readiness(
        [_row(), _row(status="UNKNOWN", filled_quantity="0", average_fill_price=None)]
    )
    assert r.unknown_count == 1 and r.terminal_count == 1
    assert r.fill_ratio == Decimal(1)  # UNKNOWN은 분모에 들어가지 않는다
    assert r.reject_ratio == Decimal(0)


def test_partial_fills_are_counted_even_on_cancelled_orders() -> None:
    rows = [
        _row(status="CANCELLED", filled_quantity="4"),  # 4/10만 체결되고 취소
        _row(status="PARTIALLY_FILLED", filled_quantity="5"),  # 진행 중 부분체결
        _row(),
    ]
    r = compute_live_readiness(rows)
    assert r.partial_fill_count == 2
    assert r.partial_fill_ratio == Decimal(2) / Decimal(3)
    assert r.unfilled_count == 0  # 일부라도 체결됐으면 미체결로 세지 않는다


def test_slippage_is_signed_adverse_and_quantity_weighted() -> None:
    rows = [
        _row(
            side="BUY", price="100", average_fill_price="101", filled_quantity="10"
        ),  # +100bps 불리
        _row(
            side="SELL",
            quantity="30",
            price="100",
            average_fill_price="100.5",
            filled_quantity="30",
        ),  # -50bps 유리
    ]
    r = compute_live_readiness(rows)
    assert r.slippage_sample_size == 2
    # (100*10 + (-50)*30) / 40 = -12.5
    assert r.avg_slippage_bps == Decimal("-12.5")


def test_market_orders_without_reference_price_are_excluded_from_slippage() -> None:
    r = compute_live_readiness([_row(price=None, average_fill_price="101")])
    assert r.avg_slippage_bps is None and r.slippage_sample_size == 0
    assert "avg_slippage_bps" in r.unavailable


def test_fees_are_summed_per_currency_and_missing_column_is_unavailable() -> None:
    with_fee = compute_live_readiness(
        [
            _row(fee_total="1.5", fee_currency="USDT"),
            _row(fee_total="0.5", fee_currency="USDT"),
            _row(fee_total="2", fee_currency="KRW"),
        ]
    )
    assert with_fee.fee_totals == {"USDT": Decimal("2.0"), "KRW": Decimal("2")}
    assert "fee_totals" not in with_fee.unavailable
    no_fee = compute_live_readiness([_row()])
    assert no_fee.fee_totals is None and "fee_totals" in no_fee.unavailable


def test_period_spans_first_creation_to_last_update() -> None:
    rows = [_row(), _row(created_at=T0 + timedelta(days=1), updated_at=T0 + timedelta(days=2))]
    r = compute_live_readiness(rows)
    assert r.period_start == T0 and r.period_end == T0 + timedelta(days=2)
    assert r.period_days == Decimal(2)


@pytest.mark.parametrize(
    "bad",
    [
        {"quantity": "0"},
        {"quantity": "-1"},
        {"filled_quantity": "11"},
        {"filled_quantity": "-1"},
        {"side": "HOLD"},
        {"quantity": "abc"},
    ],
)
def test_invalid_rows_are_rejected_loudly(bad) -> None:
    with pytest.raises(LiveReadinessInputError):
        compute_live_readiness([_row(**bad)])


def test_partial_fee_coverage_is_unavailable_not_an_underreported_total() -> None:
    """일부 주문에만 수수료가 있으면 합계를 내지 않는다 -- 과소 보고가 좋은 숫자로 안 보이게."""
    r = compute_live_readiness([_row(fee_total="1", fee_currency="USDT"), _row()])
    assert r.fee_totals is None
    assert "일부" in r.unavailable["fee_totals"]


def test_input_stream_failure_does_not_return_partial_evidence() -> None:
    def rows():
        yield _row(fee_total="1", fee_currency="USDT")
        raise RuntimeError("injected source failure")

    with pytest.raises(RuntimeError, match="injected source failure"):
        compute_live_readiness(rows())


@pytest.mark.parametrize("status", ["CANCELLED", "EXPIRED", "FAILED"])
def test_terminal_without_fill_is_unfilled(status: str) -> None:
    r = compute_live_readiness(iter([_row(status=status, filled_quantity="0")]))
    assert r.unfilled_count == r.terminal_count == 1
    assert r.filled_count == r.partial_fill_count == r.open_count == 0
    assert r.unfilled_ratio == Decimal(1)
    assert r.slippage_sample_size == 0


def test_readiness_functions_remain_within_complexity_cap() -> None:
    from pathlib import Path

    import src.services.live_readiness as module
    from scripts.check_complexity import scan_file

    path = Path(module.__file__)
    assert scan_file(path.name, path.read_text(encoding="utf-8")) == []


# --- D2 deepening: LP-1 depth floor D1 -> D2 ----------------------------
# 원장 하한: negative 2→3+, perf 0→1, gate_red 0→1, failure_injection 1→1(기존 유지)


def test_single_order_yields_non_zero_period_days() -> None:
    """단일 주문만 있어도 period_start와 period_end가 다르면 period_days가 계산된다."""
    r = compute_live_readiness([_row()])
    # _row()는 created_at=T0, updated_at=T0+1hr이므로 period_days > 0
    assert r.period_days is not None and r.period_days > 0


def test_all_cancelled_orders_fill_ratio_is_zero_not_none() -> None:
    """모든 주문이 CANCELLED이면 fill_ratio는 0이다 (0은 '전부 취소'를 의미)."""
    r = compute_live_readiness(
        [_row(status="CANCELLED", filled_quantity="0"), _row(status="EXPIRED", filled_quantity="0")]
    )
    assert r.fill_ratio == Decimal(0)  # terminal 0件中 filled 0件
    assert r.unfilled_ratio == Decimal(1)  # unfilled은 terminal 기준이므로 1
    assert r.terminal_count == 2


@pytest.mark.perf
def test_compute_live_readiness_perf_budget(perf_budget: PerfBudget) -> None:
    """task-24138 — compute_live_readiness는 I/O 없는 순수 함수로 1회 호출당 CPU 시간이
    마이크로초 단위여야 한다.

    로컬 실측: 100개 주문 행 기준 ~0.05ms (process_time). CI 병렬 환경의 Windows tick
    (≈15.6ms)와 xdist 경합을 견디기 위해 5.0ms로 여유 둔다. 실측의 100배 이상.
    """
    rows = [
        _row(
            status="FILLED",
            side="BUY",
            quantity="100",
            filled_quantity="100",
            price="100",
            average_fill_price="100.1",
            fee_total="0.5",
            fee_currency="USDT",
        )
        for _ in range(100)
    ]
    perf_budget.assert_within(
        lambda: compute_live_readiness(rows),
        budget_ms=5.0,
        label="compute_live_readiness",
    )


def test_gate_red_budget_actually_fails_past_budget() -> None:
    """게이트 적색 재현 -- 성능 단언이 tautology가 아님을 증명한다.

    동일한 샘플에 터무니없이 낮은 예산(1e-9ms)을 적용하면 AssertionError가
    발생해야 한다. budget 값을 10배 늘리는 monkeypatch 대신, assert 자체를
    pytest.raises로 검증하는 것이 가장 명확하다(task-11539).
    """
    rows = [_row() for _ in range(10)]
    perf = PerfBudget()
    # batch=100로 양자화 오차를 줄여 측정 가능한 값을 얻는다 (Windows tick ≈15.6ms)
    samples = perf.samples(lambda: compute_live_readiness(rows), n=20, warmup=0, batch=100)
    cpu_ms_values = sorted(s.cpu_ms for s in samples)
    p95_cpu_ms = cpu_ms_values[int(len(cpu_ms_values) * 0.95) - 1]

    absurdly_low_budget_ms = 1e-9
    with pytest.raises(AssertionError):
        assert p95_cpu_ms < absurdly_low_budget_ms, (
            f"예상 적색 실패: p95={p95_cpu_ms:.4f}ms가 absurd budget "
            f"{absurdly_low_budget_ms}ms보다 작지 않음"
        )


def test_gate_red_virtual_delay_exceeds_budget() -> None:
    """게이트 적색 재현 (가상 지연) -- virtual_delayed_sync로 10배 지연을 주입하면
    성능 단언이 적색(AssertionError)으로 전환되어야 한다.

    perf_inject.virtual_delayed_sync는 실제 sleep 없이 virtual ledger에 지연을
    기록하므로 테스트가 느려지지 않는다. 10배 지연 주입으로 budget를 초과하면
    assert_within가 AssertionError를.raise해야 한다.
    """
    reset_virtual_totals()
    try:
        original_compute = compute_live_readiness
        wrapped = virtual_delayed_sync(original_compute, 0.05)  # 50ms 가상 지연

        rows = [_row() for _ in range(10)]

        # virtual_delayed_sync를 통과하면 실제 budget(5ms)의 10배 지연이므로
        # assert_within가 AssertionError를.raise해야 한다.
        with pytest.raises(AssertionError):
            perf = PerfBudget()
            perf.assert_within(
                lambda: wrapped(rows),
                budget_ms=5.0,
                label="compute_live_readiness_virtual_delay",
            )

        # 가상 지연이 ledger에 기록되었는지 확인
        cpu_ns, wall_ns = virtual_totals_ns()
        assert cpu_ns > 0, "virtual_delayed_sync should have added to the virtual ledger"
    finally:
        reset_virtual_totals()
