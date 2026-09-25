"""ComputeStatement 통합테스트 — 실제 dev/test DB 대상.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §8 (PRF-001·002·009),
§9 (L49 DoD).

task-3196 DEEPEN 102: 이 리프의 기존 증빙은 negative 1건(미리컨실)뿐이었고
실패 주입·수치 성능 단언·게이트 적색 재현이 전혀 없어 D2 하한(ADR-2026-
09-09-C Decision 1, negative≥3/실패주입 1/성능단언 1/게이트적색 1)에
미달이었다. 아래 테스트가 그 공백을 메운다."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.performance.adapters.paper_input_adapter import (
    PaperStatementInputAdapter,
    UnreconciledInputError,
)
from src.foundation.performance.adapters.postgres_repository import (
    PostgresPerformanceRepository,
)
from src.foundation.performance.application.compute_statement import (
    MethodologyNotFoundError,
    compute_statement,
)
from src.foundation.performance.contracts.v1 import ComputeStatementCommand, StatementScope
from src.foundation.performance.domain.methodology import DEFAULT_METHODOLOGY
from src.foundation.performance.domain.models import (
    StatementState,
    ValuationSnapshot,
    ValuationState,
)
from src.foundation.performance.domain.rules import ScopeMixError
from tests.foundation.integration.performance.conftest import (
    create_paper_execution,
    insert_position,
    set_reconciliation_state,
)
from tests.integration.conftest import create_test_tenant

_NOW = datetime.now(timezone.utc)
_PERIOD_START = _NOW - timedelta(days=1)


@pytest.fixture
def repo(pool):
    return PostgresPerformanceRepository(pool)


@pytest.fixture
def inputs(pool):
    return PaperStatementInputAdapter(pool)


@pytest.fixture
def evidence_repo(pool):
    return PostgresAuditEventRepository(pool)


def _cmd(scope_ref: str) -> ComputeStatementCommand:
    return ComputeStatementCommand(
        scope=StatementScope.PAPER,
        scope_ref=scope_ref,
        period_start=_PERIOD_START,
        period_end=_NOW,
    )


async def test_compute_statement_raises_when_unreconciled(pool, repo, inputs, evidence_repo):
    """PRF-002 계열 — 미리컨실 입력은 계산을 거부한다(라우터가 이 예외를 409로
    매핑한다)."""
    user_id = await create_test_tenant(pool)

    with pytest.raises(UnreconciledInputError):
        await compute_statement(
            repo,
            inputs,
            evidence_repo,
            tenant_id=user_id,
            cmd=_cmd(str(user_id)),
            trace_id=uuid4(),
        )


async def test_compute_statement_marks_missing_components_pending_not_zero(
    pool, repo, inputs, evidence_repo
):
    """PRF-002 — fee/slippage/funding/fx/estimated_tax는 원장에 없어 항상
    None(PENDING)이어야 한다. 0으로 대체하지 않는다."""
    user_id = await create_test_tenant(pool)
    await set_reconciliation_state(pool, user_id, aggregate_status="HEALTHY")
    execution_id = await create_paper_execution(pool, user_id, allocated_capital=Decimal("1000"))
    await insert_position(
        pool,
        user_id,
        execution_id,
        entry_time=_NOW - timedelta(hours=1),
        realized_pnl=Decimal("2"),
        unrealized_pnl=Decimal("3"),
    )

    view = await compute_statement(
        repo, inputs, evidence_repo, tenant_id=user_id, cmd=_cmd(str(user_id)), trace_id=uuid4()
    )

    assert view.components.fees.amount is None
    assert view.components.slippage.amount is None
    assert view.components.funding.amount is None
    assert view.components.estimated_tax.amount is None
    assert view.components.gross_pnl.amount == Decimal("5")
    assert view.identity_ok is False
    assert view.state.value == "ESTIMATED"
    assert view.revision_no == 1
    assert view.methodology_hash == DEFAULT_METHODOLOGY.methodology_hash
    assert any("COMPONENTS_LEDGER_INCOMPLETE" in limitation for limitation in view.limitations)
    assert len(view.evidence_refs) == 1


async def test_compute_statement_second_call_increments_revision(pool, repo, inputs, evidence_repo):
    user_id = await create_test_tenant(pool)
    await set_reconciliation_state(pool, user_id, aggregate_status="HEALTHY")

    first = await compute_statement(
        repo, inputs, evidence_repo, tenant_id=user_id, cmd=_cmd(str(user_id)), trace_id=uuid4()
    )
    second = await compute_statement(
        repo, inputs, evidence_repo, tenant_id=user_id, cmd=_cmd(str(user_id)), trace_id=uuid4()
    )

    assert first.revision_no == 1
    assert second.revision_no == 2
    assert second.prior_statement_id == first.id


async def test_compute_statement_persists_estimated_state(pool, repo, inputs, evidence_repo):
    user_id = await create_test_tenant(pool)
    await set_reconciliation_state(pool, user_id, aggregate_status="HEALTHY")

    view = await compute_statement(
        repo, inputs, evidence_repo, tenant_id=user_id, cmd=_cmd(str(user_id)), trace_id=uuid4()
    )

    stored = await repo.get_statement(view.id)
    assert stored is not None
    assert stored.state == StatementState.ESTIMATED
    assert stored.tenant_id == user_id


async def test_compute_statement_unknown_methodology_version_raises(repo, inputs, evidence_repo):
    """negative 2 — `ComputeStatementCommand.methodology_version`이 저장소에
    없는 버전이면(오타·미배포 방법론) DEFAULT로 조용히 대체하지 않고
    `VALIDATION_METHODOLOGY_REQUIRED`로 거부한다. `inputs`/DB 상태를 전혀
    건드리기 전에(가장 먼저) 거부돼야 하므로 reconciliation_state 없이도
    검증 가능하다."""
    user_id = uuid4()
    cmd = ComputeStatementCommand(
        scope=StatementScope.PAPER,
        scope_ref=str(user_id),
        period_start=_PERIOD_START,
        period_end=_NOW,
        methodology_version="bogus-v9-does-not-exist",
    )

    with pytest.raises(MethodologyNotFoundError) as exc_info:
        await compute_statement(
            repo, inputs, evidence_repo, tenant_id=user_id, cmd=cmd, trace_id=uuid4()
        )

    assert exc_info.value.reason_code == "VALIDATION_METHODOLOGY_REQUIRED"


class _ScopeMixInputPort:
    """실패 주입용 `StatementInputPort` 대역 — `PaperStatementInputAdapter`는
    항상 `scope="PAPER"`만 반환하므로(paper_input_adapter.py L129) 실제 DB로는
    스코프 혼입을 재현할 수 없다. 여기서는 어댑터 구현 회귀(예: LIVE 지원
    추가 중 실수로 두 스코프가 섞인 snapshot을 돌려주는 경우)를 흉내내
    `compute_statement`가 `domain/rules.assert_single_scope`를 실제로
    호출하는지(단위테스트로만 검증됐던 규칙이 애플리케이션 계층에 실제
    배선됐는지) 증명한다."""

    async def load_reconciled_snapshots(
        self, *, scope_ref: str, period_start: datetime, period_end: datetime
    ) -> tuple[ValuationSnapshot, ...]:
        return (
            ValuationSnapshot(
                id=uuid4(),
                tenant_id=UUID(scope_ref),
                scope="LIVE",
                scope_ref=scope_ref,
                as_of=period_end,
                positions=(),
                cash=Decimal("0"),
                price_evidence=(),
                reconciliation_run_id=None,
                state=ValuationState.RECONCILED,
            ),
        )

    async def load_fills(
        self, *, scope_ref: str, period_start: datetime, period_end: datetime
    ) -> tuple[dict[str, object], ...]:
        return ()

    async def load_cashflows(self, *, scope_ref: str, period_start: datetime, period_end: datetime):
        return ()


async def test_compute_statement_rejects_scope_mix_from_input_port_regression(
    pool, repo, evidence_repo
):
    """negative 3 + 실패 주입 1 — 커맨드는 `scope=PAPER`인데 입력 포트가
    LIVE snapshot을 돌려주는 어댑터 회귀를 주입하면, statement를 절반만
    만들다 마는 대신(fail-closed) 아무것도 저장하지 않고 즉시
    `ScopeMixError(INTEGRITY_PAPER_LIVE_MIX)`로 거부해야 한다."""
    user_id = await create_test_tenant(pool)

    with pytest.raises(ScopeMixError) as exc_info:
        await compute_statement(
            repo,
            _ScopeMixInputPort(),
            evidence_repo,
            tenant_id=user_id,
            cmd=_cmd(str(user_id)),
            trace_id=uuid4(),
        )

    assert exc_info.value.reason_code == "INTEGRITY_PAPER_LIVE_MIX"
    stored = await repo.list_statements(tenant_id=user_id)
    assert stored == ()


# --- 수치 성능 단언 + 게이트 적색 재현 ---
#
# ADR-2026-09-09-C Decision 1 예산표에 performance statement 계산 전용 항목이
# 없어, 가장 가까운 유사 항목("5k봉 조회 p95 200ms")을 차용한다 —
# compute_statement는 단일 조회가 아니라 방법론 조회 + snapshot/fill/cashflow
# 3종 로드 + 최신 리비전 조회 + evidence 기록 + insert까지 약 10회의 순차
# 실DB 왕복을 묶은 파이프라인이라, 단일 라운드트립 예산("주문 제출→ACK p95
# 50ms")보다는 이 항목이 부하 특성상 더 가깝다(task-3148 계열 DEEPEN과 동일
# 차용 근거).
_PERF_ITERATIONS = 20
_PERF_BUDGET_MS = 200.0


async def _compute_statement_p95_ms(
    pool, repo, inputs, evidence_repo, user_id: UUID, *, n: int
) -> float:
    durations_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        await compute_statement(
            repo, inputs, evidence_repo, tenant_id=user_id, cmd=_cmd(str(user_id)), trace_id=uuid4()
        )
        durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    return durations_ms[int(len(durations_ms) * 0.95)]


@pytest.mark.perf
async def test_compute_statement_p95_latency_under_borrowed_budget(
    pool, repo, inputs, evidence_repo
):
    user_id = await create_test_tenant(pool)
    await set_reconciliation_state(pool, user_id, aggregate_status="HEALTHY")

    p95_ms = await _compute_statement_p95_ms(
        pool, repo, inputs, evidence_repo, user_id, n=_PERF_ITERATIONS
    )

    assert p95_ms < _PERF_BUDGET_MS, (
        f"compute_statement p95 지연 {p95_ms:.2f}ms가 차용 예산 "
        f"{_PERF_BUDGET_MS}ms를 초과했습니다 — 파이프라인 왕복 횟수 회귀 의심"
    )


@pytest.mark.perf
async def test_compute_statement_budget_gate_fails_on_injected_regression(
    pool, repo, inputs, evidence_repo, monkeypatch
):
    """게이트 적색 재현 — 위 p95 단언이 실제로 회귀를 잡는지 확인한다.
    `asyncpg.Connection.fetchrow` 한 번에 예산(`_PERF_BUDGET_MS`) 전체만큼
    지연을 주입하면, 파이프라인이 fetchrow를 몇 번 왕복하든(1회만 해도)
    측정치가 예산을 넘으므로 같은 측정 로직이 `AssertionError`를 내야 한다
    (tautology가 아님을 증명). 왕복 횟수 × 고정 지연으로 계산하던 이전
    방식은 왕복 수가 줄면(현재 9회 × 15ms = 135ms < 200ms) 예산 안에
    들어와 "DID NOT RAISE"로 적색이 났다 — 주입량을 예산에 묶어 왕복 수
    회귀와 무관하게 만든다. n=1: 표본 1개의 p95는 그 표본 자체다."""
    user_id = await create_test_tenant(pool)
    await set_reconciliation_state(pool, user_id, aggregate_status="HEALTHY")
    original_fetchrow = asyncpg.Connection.fetchrow
    injected_delay_s = _PERF_BUDGET_MS / 1000.0

    async def _slow_fetchrow(self: asyncpg.Connection, *args: object, **kwargs: object) -> object:
        await asyncio.sleep(injected_delay_s)
        return await original_fetchrow(self, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", _slow_fetchrow)

    p95_ms = await _compute_statement_p95_ms(pool, repo, inputs, evidence_repo, user_id, n=1)

    with pytest.raises(AssertionError):
        assert p95_ms < _PERF_BUDGET_MS
