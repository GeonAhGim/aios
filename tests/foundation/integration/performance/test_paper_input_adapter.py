"""PaperStatementInputAdapter 통합테스트 — 실제 dev/test DB 대상.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §9 (L48 DoD:
"미리컨실 409, 입력 조립").

task-3197 DEEPEN: negative가 2건(미리컨실/MATERIAL_MISMATCH)뿐이고 실패
주입·수치 성능 단언·게이트 적색 재현이 없어 D2 하한(ADR-2026-09-09-C
Decision 1)에 미달이었다. 아래 테스트가 그 공백을 메운다(compute_statement.py
DEEPEN task-3196과 동일 패턴)."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.foundation.performance.adapters.paper_input_adapter import (
    PaperStatementInputAdapter,
    UnreconciledInputError,
)
from src.foundation.performance.domain.models import ValuationState
from tests.foundation.integration.performance.conftest import (
    create_paper_execution,
    insert_filled_order,
    insert_position,
    set_reconciliation_state,
)
from tests.integration.conftest import create_test_tenant

_NOW = datetime.now(timezone.utc)
_PERIOD_START = _NOW - timedelta(days=1)


@pytest.fixture
def adapter(pool):
    return PaperStatementInputAdapter(pool)


async def test_load_reconciled_snapshots_raises_when_never_reconciled(pool, adapter):
    user_id = await create_test_tenant(pool)

    with pytest.raises(UnreconciledInputError) as exc_info:
        await adapter.load_reconciled_snapshots(
            scope_ref=str(user_id), period_start=_PERIOD_START, period_end=_NOW
        )
    assert exc_info.value.reason_code == "INTEGRITY_STATEMENT_INPUT_UNRECONCILED"


async def test_load_reconciled_snapshots_raises_when_material_mismatch(pool, adapter):
    user_id = await create_test_tenant(pool)
    await set_reconciliation_state(pool, user_id, aggregate_status="MATERIAL_MISMATCH")

    with pytest.raises(UnreconciledInputError):
        await adapter.load_reconciled_snapshots(
            scope_ref=str(user_id), period_start=_PERIOD_START, period_end=_NOW
        )


async def test_load_reconciled_snapshots_returns_snapshot_when_healthy(pool, adapter):
    user_id = await create_test_tenant(pool)
    await set_reconciliation_state(pool, user_id, aggregate_status="HEALTHY")
    execution_id = await create_paper_execution(pool, user_id, allocated_capital=Decimal("1000"))
    await insert_position(
        pool,
        user_id,
        execution_id,
        entry_time=_NOW - timedelta(hours=1),
        quantity=Decimal("2"),
        average_entry_price=Decimal("100"),
        unrealized_pnl=Decimal("5"),
        realized_pnl=Decimal("1"),
    )

    period_end = datetime.now(timezone.utc) + timedelta(minutes=1)
    snapshots = await adapter.load_reconciled_snapshots(
        scope_ref=str(user_id), period_start=_PERIOD_START, period_end=period_end
    )

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.state == ValuationState.RECONCILED
    assert snapshot.tenant_id == user_id
    assert len(snapshot.positions) == 1
    assert Decimal(snapshot.positions[0]["realized_pnl"]) == Decimal("1")
    # cash = allocated_capital(1000) - deployed_notional(2 * 100)
    assert snapshot.cash == Decimal("800")


async def test_load_reconciled_snapshots_resolved_status_is_trusted(pool, adapter):
    user_id = await create_test_tenant(pool)
    await set_reconciliation_state(pool, user_id, aggregate_status="RESOLVED")

    snapshots = await adapter.load_reconciled_snapshots(
        scope_ref=str(user_id), period_start=_PERIOD_START, period_end=_NOW
    )
    assert len(snapshots) == 1


async def test_load_fills_returns_only_filled_paper_orders(pool, adapter):
    user_id = await create_test_tenant(pool)
    execution_id = await create_paper_execution(pool, user_id)
    await insert_filled_order(
        pool,
        user_id,
        execution_id,
        average_fill_price=Decimal("123.45"),
        filled_quantity=Decimal("0.5"),
    )

    fills = await adapter.load_fills(
        scope_ref=str(user_id),
        period_start=_PERIOD_START,
        period_end=datetime.now(timezone.utc) + timedelta(minutes=1),
    )

    assert len(fills) == 1
    assert Decimal(fills[0]["average_fill_price"]) == Decimal("123.45")
    assert fills[0]["fee"] is None  # orders에 fee 컬럼이 없다 — 항상 PENDING


async def test_load_cashflows_returns_allocated_capital_as_deposit(pool, adapter):
    user_id = await create_test_tenant(pool)
    started_at = _NOW - timedelta(hours=2)
    await create_paper_execution(
        pool, user_id, allocated_capital=Decimal("500"), started_at=started_at
    )

    cashflows = await adapter.load_cashflows(
        scope_ref=str(user_id), period_start=_PERIOD_START, period_end=_NOW
    )

    assert len(cashflows) == 1
    assert cashflows[0].amount == Decimal("500")
    assert cashflows[0].kind.value == "DEPOSIT"


# --- negative 3: 잘못된 scope_ref 형식 ---


async def test_load_reconciled_snapshots_raises_on_malformed_scope_ref(adapter):
    """negative 3 — `scope_ref`가 UUID 형식이 아니면(손상된 값·연동 버그)
    DB를 건드리기도 전에 `ValueError`로 거부한다. 잘못된 스코프로 조용히
    빈 스냅샷을 만들지 않는다."""
    with pytest.raises(ValueError):
        await adapter.load_reconciled_snapshots(
            scope_ref="not-a-uuid", period_start=_PERIOD_START, period_end=_NOW
        )


# --- 실패 주입: positions 조회 중 DB 오류는 삼키지 않고 그대로 전파 ---


async def test_load_reconciled_snapshots_propagates_db_failure_without_silent_default(
    pool, adapter, monkeypatch
):
    """실패 주입 — reconciliation 체크(HEALTHY)를 통과한 뒤 positions 조회에서
    실제 DB 오류(연결 끊김 등)가 나면, 어댑터가 이를 삼켜 빈 포지션의 "정상"
    스냅샷으로 위장하지 않고 예외를 그대로 전파해야 한다(fail-closed)."""
    user_id = await create_test_tenant(pool)
    await set_reconciliation_state(pool, user_id, aggregate_status="HEALTHY")
    call_count = 0

    async def _failing_fetch(self: asyncpg.Connection, *args: object, **kwargs: object) -> object:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("simulated connection loss")

    monkeypatch.setattr(asyncpg.Connection, "fetch", _failing_fetch)

    with pytest.raises(RuntimeError, match="simulated connection loss"):
        await adapter.load_reconciled_snapshots(
            scope_ref=str(user_id), period_start=_PERIOD_START, period_end=_NOW
        )
    assert call_count == 1


# --- 수치 성능 단언 + 게이트 적색 재현 ---
#
# ADR-2026-09-09-C Decision 1 예산표에 이 어댑터 전용 항목이 없다.
# `load_reconciled_snapshots`는 단일 조회가 아니라 reconciliation_state 체크
# (fetchrow) + 최신 run_id 조회(fetchrow) + positions(fetch) + capital 합계
# (fetchrow) 순차 실DB 왕복 4회를 한 커넥션 안에서 묶은 호출이라, 단일
# 라운드트립 예산("주문 제출→ACK p95 50ms")과 compute_statement의 ~10회
# 왕복 파이프라인 예산(200ms, task-3196) 사이 어딘가가 맞다 — 왕복 횟수
# 비율로 완만하게 내삽해 100ms를 이 리프의 예산으로 차용한다.
_PERF_ITERATIONS = 20
_PERF_BUDGET_MS = 100.0


async def _load_snapshots_p95_ms(adapter: PaperStatementInputAdapter, user_id, *, n: int) -> float:
    durations_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        await adapter.load_reconciled_snapshots(
            scope_ref=str(user_id), period_start=_PERIOD_START, period_end=_NOW
        )
        durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    return durations_ms[int(len(durations_ms) * 0.95)]


async def test_load_reconciled_snapshots_p95_latency_under_borrowed_budget(pool, adapter):
    user_id = await create_test_tenant(pool)
    await set_reconciliation_state(pool, user_id, aggregate_status="HEALTHY")

    p95_ms = await _load_snapshots_p95_ms(adapter, user_id, n=_PERF_ITERATIONS)

    assert p95_ms < _PERF_BUDGET_MS, (
        f"load_reconciled_snapshots p95 지연 {p95_ms:.2f}ms가 차용 예산 "
        f"{_PERF_BUDGET_MS}ms를 초과했습니다 — 쿼리 왕복 횟수 회귀 의심"
    )


async def test_load_reconciled_snapshots_budget_gate_fails_on_injected_regression(
    pool, adapter, monkeypatch
):
    """게이트 적색 재현 — 위 p95 단언이 실제로 회귀를 잡는지 확인한다.
    `asyncpg.Connection.fetchrow`에 40ms 인위 지연을 주입하면(파이프라인이
    fetchrow만으로도 3회 왕복하므로 합산 120ms+, 예산 100ms 초과), 같은
    측정 로직이 실제로 `AssertionError`를 내는지 본다(tautology가 아님을
    증명)."""
    user_id = await create_test_tenant(pool)
    await set_reconciliation_state(pool, user_id, aggregate_status="HEALTHY")
    original_fetchrow = asyncpg.Connection.fetchrow

    async def _slow_fetchrow(self: asyncpg.Connection, *args: object, **kwargs: object) -> object:
        await asyncio.sleep(0.04)
        return await original_fetchrow(self, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", _slow_fetchrow)

    p95_ms = await _load_snapshots_p95_ms(adapter, user_id, n=3)

    with pytest.raises(AssertionError):
        assert p95_ms < _PERF_BUDGET_MS
