"""TWR(시간가중수익률) — 기간연결(현금흐름 기초 반영) 정확값.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §8 (L46 DoD)."""
from __future__ import annotations

import statistics
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.foundation.performance.domain import twr as twr_module
from src.foundation.performance.domain.models import Cashflow, CashflowKind
from src.foundation.performance.domain.twr import MissingInputError, twr

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_T1 = _T0 + timedelta(days=15)
_T2 = _T0 + timedelta(days=30)

# ADR-2026-09-09-C Decision 1에는 순수 성과 산식 전용 예산이 없어,
# 월간 명세서 계산(성과 계산 p95 < 3s)보다 충분히 작은 100ms를 단위 예산으로 둔다.
_TWR_LATENCY_BUDGET_MS = 100
_MONTHLY_VALUATIONS = [
    (_T0 + timedelta(days=30 * index), Decimal("1000") + Decimal(index * 10))
    for index in range(13)
]
_MONTHLY_CASHFLOWS = [
    Cashflow(
        at=at,
        amount=Decimal("25"),
        kind=CashflowKind.DEPOSIT if index % 2 == 0 else CashflowKind.WITHDRAWAL,
    )
    for index, (at, _value) in enumerate(_MONTHLY_VALUATIONS[1:-1])
]


def _p95_latency_ms(samples: int = 20) -> float:
    latencies_ms: list[float] = []
    for _ in range(samples):
        start = time.perf_counter()
        twr(_MONTHLY_VALUATIONS, _MONTHLY_CASHFLOWS)
        latencies_ms.append((time.perf_counter() - start) * 1000)
    return statistics.quantiles(latencies_ms, n=20)[18]  # p95


def test_twr_meets_latency_budget():
    """수치 성능 단언 — 월간 명세서 규모 TWR의 p95가 예산 안이어야 한다."""
    assert _p95_latency_ms() < _TWR_LATENCY_BUDGET_MS


def test_twr_latency_budget_actually_fails_when_calculation_regresses(monkeypatch):
    """게이트 적색 재현 — 현금흐름 부호 계산 지연을 주입하면 성능 단언이 적색이어야 한다."""
    original_signed = twr_module._signed

    def _slow_signed(cashflow: Cashflow) -> Decimal:
        time.sleep(0.01)
        return original_signed(cashflow)

    monkeypatch.setattr(twr_module, "_signed", _slow_signed)

    latency_ms = _p95_latency_ms(samples=3)
    with pytest.raises(AssertionError):
        assert latency_ms < _TWR_LATENCY_BUDGET_MS


def test_twr_propagates_cashflow_sign_dependency_failure(monkeypatch):
    """실패주입 — 부호 변환 의존성 실패를 조용히 0으로 대체하지 않는다."""
    failure = RuntimeError("injected cashflow sign failure")

    def _broken_signed(_cashflow: Cashflow) -> Decimal:
        raise failure

    monkeypatch.setattr(twr_module, "_signed", _broken_signed)
    with pytest.raises(RuntimeError, match="injected cashflow sign failure") as exc:
        twr(
            [(_T0, Decimal("1000")), (_T1, Decimal("1100"))],
            [Cashflow(at=_T0, amount=Decimal("100"), kind=CashflowKind.DEPOSIT)],
        )
    assert exc.value is failure


def test_twr_rejects_zero_investable_base():
    """현금흐름 반영 후 기초자산이 0이면 정의되지 않은 수익률을 거부한다."""
    with pytest.raises(MissingInputError) as excinfo:
        twr(
            [(_T0, Decimal("100")), (_T1, Decimal("100"))],
            [Cashflow(at=_T0, amount=Decimal("100"), kind=CashflowKind.WITHDRAWAL)],
        )
    assert excinfo.value.reason_code == "INTEGRITY_STATEMENT_INPUT_UNRECONCILED"


def test_twr_links_two_subperiods_with_a_deposit_at_the_boundary():
    """1000 -> (10%) -> 1100, 그 시점에 200 입금(원금 1300) -> (10%) -> 1430.
    연결 수익률 = 1.1*1.1-1 = 0.21."""
    valuations = [(_T0, Decimal("1000")), (_T1, Decimal("1100")), (_T2, Decimal("1430"))]
    cashflows = [Cashflow(at=_T1, amount=Decimal("200"), kind=CashflowKind.DEPOSIT)]

    result = twr(valuations, cashflows)

    assert result == Decimal("0.21")


def test_twr_with_no_cashflows_is_simple_return():
    valuations = [(_T0, Decimal("1000")), (_T2, Decimal("1100"))]
    assert twr(valuations, []) == Decimal("0.1")


def test_twr_withdrawal_reduces_base():
    """400 출금 후 원금 = 1100-400=700, 700 -> 770(10%)."""
    valuations = [(_T0, Decimal("1000")), (_T1, Decimal("1100")), (_T2, Decimal("770"))]
    cashflows = [Cashflow(at=_T1, amount=Decimal("400"), kind=CashflowKind.WITHDRAWAL)]

    result = twr(valuations, cashflows)

    assert result == Decimal("0.21")


def test_twr_rejects_cashflow_without_matching_valuation():
    """현금흐름 시점에 평가액이 없으면 근사하지 않고 명시적으로 거부한다."""
    valuations = [(_T0, Decimal("1000")), (_T2, Decimal("1100"))]
    unmatched_time = _T0 + timedelta(days=7)
    cashflows = [Cashflow(at=unmatched_time, amount=Decimal("50"), kind=CashflowKind.DEPOSIT)]

    with pytest.raises(MissingInputError) as excinfo:
        twr(valuations, cashflows)
    assert excinfo.value.reason_code == "INTEGRITY_STATEMENT_INPUT_UNRECONCILED"


def test_twr_requires_at_least_two_valuations():
    with pytest.raises(MissingInputError):
        twr([(_T0, Decimal("1000"))], [])
