"""MWR(금액가중수익률, IRR 이분법) — 알려진 값과 수렴 실패 케이스.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §9 (L46 DoD)."""

from __future__ import annotations

import statistics
import time
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.foundation.performance.domain import mwr as mwr_module
from src.foundation.performance.domain.models import Cashflow, CashflowKind
from src.foundation.performance.domain.mwr import mwr

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
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


def _p95_latency_ms(samples: int = 20) -> float:
    latencies_ms: list[float] = []
    for _ in range(samples):
        start = time.perf_counter()
        mwr(_MWR_MONTHLY_CASHFLOWS, Decimal("1000"), Decimal("1200"), _T0, _T1)
        latencies_ms.append((time.perf_counter() - start) * 1000)
    return statistics.quantiles(latencies_ms, n=20)[18]  # p95


def test_mwr_bisection_meets_latency_budget():
    """수치 성능 단언 — 월간 명세서 규모(현금흐름 11건) 이분법 호출의
    p95 지연이 예산(100ms) 안에 있어야 한다."""
    assert _p95_latency_ms() < _MWR_LATENCY_BUDGET_MS


def test_mwr_latency_budget_actually_fails_when_bisection_regresses(monkeypatch):
    """게이트 적색 재현 — `_npv`에 인위 지연을 주입해 위 성능 단언이 실제로
    AssertionError를 내는지 확인한다(tautology가 아님을 증명, ee9e5ff1의
    compute_statement 지연 주입과 같은 패턴)."""
    original_npv = mwr_module._npv

    def _slow_npv(rate: Decimal, flows: Sequence[tuple[Decimal, Decimal]]) -> Decimal:
        time.sleep(0.01)
        return original_npv(rate, flows)

    monkeypatch.setattr(mwr_module, "_npv", _slow_npv)

    latency_ms = _p95_latency_ms(samples=3)
    with pytest.raises(AssertionError):
        assert latency_ms < _MWR_LATENCY_BUDGET_MS


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


def test_mwr_red_gate_propagates_npv_assertion(monkeypatch):
    """계산 오류가 지연 예산 초과로 오인되어 적색 재현을 통과하면 안 된다."""
    failure = AssertionError("injected NPV calculation failure")

    def _broken_npv(rate: Decimal, flows: Sequence[tuple[Decimal, Decimal]]) -> Decimal:
        raise failure

    monkeypatch.setattr(mwr_module, "_npv", _broken_npv)
    with pytest.raises(AssertionError, match="injected NPV calculation failure") as exc:
        test_mwr_latency_budget_actually_fails_when_bisection_regresses(monkeypatch)
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
