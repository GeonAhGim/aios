"""MWR(금액가중수익률, IRR 이분법) — 알려진 값과 수렴 실패 케이스.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §9 (L46 DoD)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.foundation.performance.domain import mwr as mwr_module
from src.foundation.performance.domain.models import Cashflow, CashflowKind
from src.foundation.performance.domain.mwr import mwr
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_T1 = _T0 + timedelta(days=365)

# ADR-2026-09-09-C Decision 1 예산표에 순수 계산 함수 전용 항목이 없다 —
# 가장 가까운 유사 항목("5k봉 조회 p95 200ms", 단일 호출 왕복)을 차용한다.
# mwr()의 이분법은 호출 1회당 NPV를 최대 200번 재평가하고 각 NPV는
# 현금흐름 개수(여기서는 실측 fixture와 같은 월간 명세서 규모 12건)만큼
# `Decimal ** Decimal`(비정수 지수, 내부적으로 ln/exp 경유라 고가)을
# 계산한다 — 실측 p95 약 17ms(정상 케이스), 예산 100ms로 5배 이상 여유.
_MWR_LATENCY_BUDGET_MS = 100
_MWR_MONTHLY_CASHFLOWS = [
    Cashflow(
        at=_T0 + timedelta(days=30 * i),
        amount=Decimal("50"),
        kind=CashflowKind.DEPOSIT if i % 2 == 0 else CashflowKind.WITHDRAWAL,
    )
    for i in range(1, 12)
]


# ── PerfBudget 기반 성능 단언 (D2 성능 단언 0 → 보강) ────────────────────


@pytest.mark.perf
def test_mwr_bisection_meets_latency_budget(perf_budget: PerfBudget):
    """수치 성능 단언 — 월간 명세서 규모(현금흐름 11건) 이분법 호출의
    CPU 지연이 예산(100ms) 안에 있어야 한다. PerfBudget.best_of로
    scheduler 노이즈를 걷어낸다."""
    perf_budget.assert_within(
        lambda: mwr(_MWR_MONTHLY_CASHFLOWS, Decimal("1000"), Decimal("1200"), _T0, _T1),
        budget_ms=_MWR_LATENCY_BUDGET_MS,
        n=5,
        label="mwr bisection p95",
    )


def _inject_npv_delay(monkeypatch, delay_seconds: float) -> None:
    """Helper: inject virtual delay into `_npv` for gate-red reproduction."""
    reset_virtual_totals()
    original_npv = mwr_module._npv

    def _slow_npv(rate: Decimal, flows: Sequence[tuple[Decimal, Decimal]]) -> Decimal:
        # delay_seconds는 1회 NPV 계산당 주입 지연 — 10배(5ms)가 표준
        return virtual_delayed_sync(original_npv, delay_seconds)(rate, flows)

    monkeypatch.setattr(mwr_module, "_npv", _slow_npv)


@pytest.mark.perf
def test_mwr_latency_budget_actually_fails_when_bisection_regresses(
    perf_budget: PerfBudget, monkeypatch
):
    """게이트 적색 재현 — `_npv`에 virtual_delayed_sync 10배 지연을 주입해
    성능 단언이 실제로 AssertionError를 내는지 확인한다(tautology 방지).
    virtual_delayed_sync은 실제 sleep 없이 가상 지연 ledger에만 기록하므로
    테스트가 느려지지 않는다."""
    _inject_npv_delay(monkeypatch, 0.005)

    # 11건 현금흐름 + start_value + end_value = 13건 NPV 계산 × 200 이분법 루프
    # 각 NPV에 5ms 지연 → 1회 mwr 호출당 약 13×200×5ms = 13초 수준으로 느려짐
    # 가상 지연 ledger가 PerfBudget.sample에서 자동 합산됨
    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            lambda: mwr(_MWR_MONTHLY_CASHFLOWS, Decimal("1000"), Decimal("1200"), _T0, _T1),
            budget_ms=_MWR_LATENCY_BUDGET_MS,
            n=3,
            label="mwr regress gate-red",
        )


def test_mwr_with_no_interim_cashflows_matches_simple_return():
    """현금흐름 없이 1000 -> 1100이면 IRR은 단순수익률 10%와 같아야 한다."""
    result = mwr([], Decimal("1000"), Decimal("1100"), _T0, _T1)

    assert result is not None
    assert abs(result - Decimal("0.1")) < Decimal("1E-8")


def test_mwr_with_known_deposit_matches_expected_irr():
    """1000 투자, 중간에 500 입금, 종료값 1650일 때의 IRR을 npv=0 재확인으로
    검증한다(폐형 공식 대신 결과를 직접 npv에 대입해 0에 가까운지 확인 —
    이분법 자체의 정답을 재현하는 회귀 테스트)."""
    mid = _T0 + timedelta(days=182)
    cashflows = [Cashflow(at=mid, amount=Decimal("500"), kind=CashflowKind.DEPOSIT)]

    result = mwr(cashflows, Decimal("1000"), Decimal("1650"), _T0, _T1)

    assert result is not None
    t_mid = Decimal(182) / Decimal(365)
    npv = (
        -Decimal("1000")
        - Decimal("500") / (Decimal(1) + result) ** t_mid
        + Decimal("1650") / (Decimal(1) + result) ** Decimal(1)
    )
    assert abs(npv) < Decimal("1E-6")


def test_mwr_returns_none_when_period_has_zero_duration():
    assert mwr([], Decimal("1000"), Decimal("1100"), _T0, _T0) is None


def test_mwr_returns_none_when_start_value_is_not_positive():
    assert mwr([], Decimal("0"), Decimal("1100"), _T0, _T1) is None


def test_mwr_red_gate_propagates_npv_assertion(perf_budget: PerfBudget, monkeypatch):
    """계산 오류가 지연 예산 초과로 오인되어 적색 재현을 통과하면 안 된다.
    _npv가 AssertionError를 raise하면 PerfBudget.assert_within의 단언이
    아니라 함수 자체의 예외로 올라와야 함을 확인한다."""
    failure = AssertionError("injected NPV calculation failure")

    def _broken_npv(rate: Decimal, flows: Sequence[tuple[Decimal, Decimal]]) -> Decimal:
        raise failure

    monkeypatch.setattr(mwr_module, "_npv", _broken_npv)
    with pytest.raises(AssertionError, match="injected NPV calculation failure") as exc:
        perf_budget.assert_within(
            lambda: mwr(_MWR_MONTHLY_CASHFLOWS, Decimal("1000"), Decimal("1200"), _T0, _T1),
            budget_ms=_MWR_LATENCY_BUDGET_MS,
            n=1,
            label="mwr npv failure",
        )
    assert exc.value is failure


def test_mwr_returns_none_when_root_is_outside_bracket():
    assert mwr([], Decimal("1000"), Decimal("12000"), _T0, _T1) is None


def test_mwr_returns_none_when_iteration_budget_is_exhausted(monkeypatch):
    monkeypatch.setattr(mwr_module, "MWR_MAX_ITERATIONS", 1)
    assert mwr([], Decimal("1000"), Decimal("1100"), _T0, _T1) is None


def test_mwr_returns_none_when_end_value_is_negative():
    """종료값이 음수이면 현금흐름 부호가 모두 같아져 이분법 괄호 밖에서
    시작한다 — 수렴 불가. start_value>0, end_value<0일 때 NPV의 부호가
    low/high에서 모두 양수이므로 None을 반환해야 한다."""
    assert mwr([], Decimal("1000"), Decimal("-500"), _T0, _T1) is None


def test_mwr_skips_cashflow_before_period_start():
    """기간 시작 전 현금흐름은 silently skip된다 — elapsed<0 조건.
    이 테스트가 없으면 skip되지 않을 때 디버깅이 어려워진다."""
    early_cf = Cashflow(
        at=_T0 - timedelta(days=10),
        amount=Decimal("100"),
        kind=CashflowKind.DEPOSIT,
    )
    # early cashflow이 skip되더라도 start_value/end_value만 남으므로
    # 정상 수렴(단순수익률)이 나와야 한다.
    result = mwr([early_cf], Decimal("1000"), Decimal("1100"), _T0, _T1)
    assert result is not None
    # skip되었으므로 단순수익률 10%와 같아야 함
    assert abs(result - Decimal("0.1")) < Decimal("1E-8")


def test_mwr_skips_cashflow_after_period_end():
    """기간 종료 후 현금흐름은 silently skip된다 — elapsed>total_seconds
    조건. 시작 전 테스트와 대칭 케이스."""
    late_cf = Cashflow(
        at=_T1 + timedelta(days=10),
        amount=Decimal("100"),
        kind=CashflowKind.DEPOSIT,
    )
    result = mwr([late_cf], Decimal("1000"), Decimal("1100"), _T0, _T1)
    assert result is not None
    assert abs(result - Decimal("0.1")) < Decimal("1E-8")
