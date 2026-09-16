"""도메인 → 계약 매핑(`statement_projection.py`) 단위테스트 — DEEPEN(task-3191).

task-1759(commit b3957486)가 `# type: ignore` 억제 대신 실제 값 검증으로 바꾼
`_return_basis`/`_return_method` 좁히기 분기는 그 이후 한 번도 테스트 대상이
되지 않았다 — 저장소에 이 모듈을 겨냥한 테스트 파일 자체가 없었다. 이 리프가
그 공백을 메운다.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6/§3.4.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.performance.application import statement_projection as sp
from src.foundation.performance.domain.models import (
    ComponentBreakdown,
    PerformanceStatement,
    ReturnFigure,
    StatementState,
)

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_T1 = _T0 + timedelta(days=30)


def _breakdown() -> ComponentBreakdown:
    return ComponentBreakdown(
        gross_pnl=Decimal("120"),
        fees=Decimal("10"),
        slippage=Decimal("5"),
        funding=Decimal("2"),
        fx=Decimal("0"),
        cashflows_net=Decimal("0"),
        estimated_tax=Decimal("3"),
        net_pnl=Decimal("100"),
    )


def _statement(
    *,
    state: StatementState = StatementState.FINAL,
    basis: str = "GROSS",
    method: str = "TWR",
    n_returns: int = 1,
) -> PerformanceStatement:
    returns = tuple(
        ReturnFigure(
            value_pct=Decimal("1.5"),
            basis=basis,
            method=method,
            period_start=_T0,
            period_end=_T1,
            annualized=False,
            periods_per_year=None,
        )
        for _ in range(n_returns)
    )
    return PerformanceStatement(
        id=uuid4(),
        tenant_id=uuid4(),
        scope="PAPER",
        scope_ref="acct-1",
        period_start=_T0,
        period_end=_T1,
        as_of=_T1,
        methodology_version="pm-v1",
        methodology_hash="hash",
        input_refs=("snap-1",),
        components=_breakdown(),
        returns=returns,
        risk={},
        benchmark=None,
        benchmark_ref=None,
        state=state,
        revision_no=1,
        prior_statement_id=None,
        identity_ok=True,
        identity_residual=Decimal("0"),
    )


# ---------- 값 좁히기 분기(negative, task-1759) ----------


def test_return_basis_rejects_unrecognised_value():
    """negative — task-1759가 `# type: ignore`를 대체한 값 검증 분기. 이전에는
    이 분기를 겨냥한 테스트가 저장소에 전혀 없었다."""
    with pytest.raises(ValueError, match="invalid return basis"):
        sp._return_basis("BOGUS")


def test_return_method_rejects_unrecognised_value():
    """negative — 위와 대칭인 `_return_method` 분기."""
    with pytest.raises(ValueError, match="invalid return method"):
        sp._return_method("BOGUS")


@pytest.mark.parametrize("basis", ["GROSS", "NET"])
def test_return_basis_accepts_known_values(basis: str):
    assert sp._return_basis(basis) == basis


@pytest.mark.parametrize("method", ["TWR", "MWR"])
def test_return_method_accepts_known_values(method: str):
    assert sp._return_method(method) == method


def test_statement_to_view_maps_final_state_to_final_money_state():
    statement = _statement(state=StatementState.FINAL)

    view = sp.statement_to_view(statement)

    assert view.components.gross_pnl.state == "FINAL"
    assert view.returns[0].basis == "GROSS"
    assert view.returns[0].method == "TWR"


def test_statement_to_view_maps_estimated_state_to_estimated_money_state():
    statement = _statement(state=StatementState.ESTIMATED)

    view = sp.statement_to_view(statement)

    assert view.components.gross_pnl.state == "ESTIMATED"


# ---------- 실패 주입(failure injection) ----------


def test_statement_to_view_fails_closed_on_corrupted_return_basis():
    """실패 주입 — 마이그레이션 결함·수기 DB 수정 등으로 저장된 `returns.basis`가
    계약이 허용하는 GROSS/NET을 벗어나면, `# type: ignore`로 억눌러 pydantic이
    나중에 불투명하게 실패하게 두지 않고 이 경계에서 곧바로 원인을 드러낸다
    (fail-closed, 71번 §4 domain/contract 경계 원칙)."""
    statement = _statement(basis="INVALID_BASIS")

    with pytest.raises(ValueError, match="invalid return basis"):
        sp.statement_to_view(statement)


def test_statement_to_view_fails_closed_on_corrupted_return_method():
    """실패 주입 — 위와 대칭(`method` 경계 오염)."""
    statement = _statement(method="INVALID_METHOD")

    with pytest.raises(ValueError, match="invalid return method"):
        sp.statement_to_view(statement)


# ---------- 수치 성능 단언(D2) ----------

_PROJECTION_COUNT = 2000
_PROJECTION_BUDGET_SECONDS = 3.0
_PROJECTION_MIN_PER_SEC = 300.0


def test_statement_to_view_bulk_conversion_within_latency_budget():
    """수치 성능 게이트(D2) — 대량 statement 변환의 총 처리시간/처리량을
    단언한다(print 아님). task-1759가 추가한 값 검증 분기가 핫패스에서
    반복 호출돼도 눈에 띄는 회귀가 없어야 한다."""
    statement = _statement(n_returns=4)

    started = time.perf_counter()
    for _ in range(_PROJECTION_COUNT):
        sp.statement_to_view(statement)
    elapsed = time.perf_counter() - started

    throughput = _PROJECTION_COUNT / elapsed
    print(
        f"\nstatement_to_view throughput: {throughput:.0f}/s "
        f"({_PROJECTION_COUNT}건 / {elapsed * 1000:.1f}ms)"
    )
    assert elapsed <= _PROJECTION_BUDGET_SECONDS, (
        f"{_PROJECTION_COUNT}건 변환에 {elapsed:.3f}s — "
        f"예산({_PROJECTION_BUDGET_SECONDS}s) 초과: 핫패스 회귀 의심"
    )
    assert throughput >= _PROJECTION_MIN_PER_SEC, (
        f"처리량({throughput:.0f}/s)이 하한({_PROJECTION_MIN_PER_SEC}/s) 미달"
    )


def test_perf_budget_assertion_actually_trips_on_regression(monkeypatch):
    """게이트 적색 재현(D2) — 위 성능 단언이 tautology가 아님을 증명한다.
    `_return_basis`에 인위적 지연을 주입하면 동일한 형태의 예산 단언이 실제로
    AssertionError를 낸다."""
    original_return_basis = sp._return_basis

    def slow_return_basis(value: str) -> str:
        time.sleep(0.002)
        return original_return_basis(value)

    monkeypatch.setattr(sp, "_return_basis", slow_return_basis)
    statement = _statement(n_returns=1)

    started = time.perf_counter()
    for _ in range(50):
        sp.statement_to_view(statement)
    elapsed = time.perf_counter() - started

    with pytest.raises(AssertionError):
        assert elapsed <= 0.001  # 50 * 2ms 인위 지연 >> 예산 — 반드시 적색
