"""ComputeStatement 커맨드 — 81번 §2 파이프라인.

select reconciled snapshots/fills/cashflows → apply methodology version →
value positions → compute costs/returns/risk → validate identity → persist
immutable statement + evidence.

한계(명시, paper_input_adapter.py의 스콥 축소를 그대로 물려받는다):
`StatementInputPort`가 항상 스냅샷을 정확히 1개(현재 시점)만 주므로 TWR/MWR
계산에 필요한 경계값 2개를 채울 수 없다 — `returns`는 항상 `value_pct=None`
(PENDING)이다. `fees`/`slippage`/`funding`/`fx`/`estimated_tax`도 원장에
해당 컬럼이 없어 항상 `None`이다. `gross_pnl`(포지션 realized+unrealized
합)과 `cashflows_net`(실행 시작 시 allocated_capital 합)만 실제로 채워진다.
0으로 대체하지 않는다(PRF-002) — 이 리프가 실제로 검증하는 건 "입력이
부족하면 억지로 항등식을 통과시키지 않는다"는 이 규율 자체다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from src.foundation.evidence.application.record_command_event import record_command_event
from src.foundation.evidence.ports.repository import AuditEventRepository
from src.foundation.performance.application.statement_projection import statement_to_view
from src.foundation.performance.contracts.v1 import (
    ComputeStatementCommand,
    PerformanceStatementView,
)
from src.foundation.performance.domain.methodology import DEFAULT_METHODOLOGY
from src.foundation.performance.domain.models import (
    Cashflow,
    CashflowKind,
    ComponentBreakdown,
    PerformanceStatement,
    ReturnFigure,
    StatementState,
    ValuationSnapshot,
)
from src.foundation.performance.domain.rules import assert_single_scope, next_revision
from src.foundation.performance.ports.repository import PerformanceRepository, StatementInputPort

_INSUFFICIENT_VALUATION_LIMITATION = (
    "IDENTITY_INSUFFICIENT_VALUATION_HISTORY: 기간 경계 평가액이 1개뿐이라 "
    "회계 항등식·TWR/MWR을 계산할 수 없습니다(추정치 아님, 미계산)."
)
_MISSING_LEDGER_LIMITATION = (
    "COMPONENTS_LEDGER_INCOMPLETE: fees/slippage/funding/fx/estimated_tax는 "
    "현재 원장에 컬럼이 없어 항상 PENDING입니다."
)


class MethodologyNotFoundError(Exception):
    def __init__(self, version: str) -> None:
        super().__init__(f"VALIDATION_METHODOLOGY_REQUIRED: {version}")
        self.reason_code = "VALIDATION_METHODOLOGY_REQUIRED"


def _signed_cashflow_total(cashflows: tuple[Cashflow, ...]) -> Decimal:
    total = Decimal(0)
    for cf in cashflows:
        total += cf.amount if cf.kind == CashflowKind.DEPOSIT else -cf.amount
    return total


def _gross_pnl(snapshots: tuple[ValuationSnapshot, ...]) -> Decimal | None:
    if not snapshots:
        return None
    latest = max(snapshots, key=lambda s: s.as_of)
    total = Decimal(0)
    for position in latest.positions:
        total += Decimal(str(position["realized_pnl"])) + Decimal(str(position["unrealized_pnl"]))
    return total


async def compute_statement(
    repo: PerformanceRepository,
    inputs: StatementInputPort,
    evidence_repo: AuditEventRepository | None,
    *,
    tenant_id: UUID,
    cmd: ComputeStatementCommand,
    trace_id: UUID,
) -> PerformanceStatementView:
    methodology_version = cmd.methodology_version or DEFAULT_METHODOLOGY.version
    methodology = await repo.get_methodology(methodology_version)
    if methodology is None:
        if methodology_version != DEFAULT_METHODOLOGY.version:
            raise MethodologyNotFoundError(methodology_version)
        methodology = await repo.insert_methodology(DEFAULT_METHODOLOGY)

    snapshots = await inputs.load_reconciled_snapshots(
        scope_ref=cmd.scope_ref, period_start=cmd.period_start, period_end=cmd.period_end
    )
    fills = await inputs.load_fills(
        scope_ref=cmd.scope_ref, period_start=cmd.period_start, period_end=cmd.period_end
    )
    cashflows = await inputs.load_cashflows(
        scope_ref=cmd.scope_ref, period_start=cmd.period_start, period_end=cmd.period_end
    )
    assert_single_scope((cmd.scope.value, *(s.scope for s in snapshots)))

    gross_pnl = _gross_pnl(snapshots)
    cashflows_net = _signed_cashflow_total(cashflows)
    components = ComponentBreakdown(
        gross_pnl=gross_pnl,
        fees=None,
        slippage=None,
        funding=None,
        fx=None,
        cashflows_net=cashflows_net,
        estimated_tax=None,
        net_pnl=None,
    )

    limitations = [_MISSING_LEDGER_LIMITATION]
    if len(snapshots) < 2:
        limitations.append(_INSUFFICIENT_VALUATION_LIMITATION)
    identity_ok = False
    identity_residual: Decimal | None = None

    input_refs = [f"snapshot:{s.id}" for s in snapshots]
    input_refs.extend(f"fill:{f['order_id']}" for f in fills)
    input_refs.append(f"trace:{trace_id}")

    prior = await repo.get_latest_statement(
        tenant_id=tenant_id,
        scope=cmd.scope.value,
        scope_ref=cmd.scope_ref,
        period_start=cmd.period_start,
        period_end=cmd.period_end,
        methodology_version=methodology_version,
    )
    revision_no = next_revision(prior.revision_no) if prior is not None else 1
    statement_id = uuid4()

    evidence_refs: tuple[str, ...] = ()
    if evidence_repo is not None:
        # WORM 테이블(performance_statement)은 UPDATE가 없다 — insert 뒤에
        # evidence_refs를 채우려고 다시 insert하면 UNIQUE(revision_no) 충돌이
        # 난다. 그래서 statement_id를 먼저 확정해 감사 이벤트를 statement
        # insert *이전에* 기록하고, 그 event.id를 evidence_refs에 담아 단
        # 한 번만 insert한다.
        event = await record_command_event(
            evidence_repo,
            tenant_id=tenant_id,
            aggregate_type="performance_statement",
            aggregate_id=statement_id,
            action="performance.statement_computed.v1",
            actor_subject_id=tenant_id,
            payload={
                "scope": cmd.scope.value,
                "scope_ref": cmd.scope_ref,
                "revision_no": revision_no,
                "trace_id": str(trace_id),
            },
        )
        evidence_refs = (f"audit:{event.id}",)

    statement = PerformanceStatement(
        id=statement_id,
        tenant_id=tenant_id,
        scope=cmd.scope.value,
        scope_ref=cmd.scope_ref,
        period_start=cmd.period_start,
        period_end=cmd.period_end,
        as_of=datetime.now(timezone.utc),
        methodology_version=methodology.version,
        methodology_hash=methodology.methodology_hash,
        input_refs=tuple(input_refs),
        components=components,
        returns=(
            ReturnFigure(
                value_pct=None,
                basis="NET",
                method="TWR",
                period_start=cmd.period_start,
                period_end=cmd.period_end,
                annualized=False,
                periods_per_year=methodology.periods_per_year,
            ),
        ),
        risk={"vol_pct": None, "mdd_pct": None, "sharpe": None, "calmar": None},
        benchmark=None,
        benchmark_ref=None,
        state=StatementState.ESTIMATED,
        revision_no=revision_no,
        prior_statement_id=prior.id if prior is not None else None,
        identity_ok=identity_ok,
        identity_residual=identity_residual,
        limitations=tuple(limitations),
        evidence_refs=evidence_refs,
    )

    saved = await repo.insert_statement(statement)
    return statement_to_view(saved)
