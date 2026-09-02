"""Performance Reporting repository/input 포트. domain은 이 Protocol만 알고,
실제 구현(adapters/)은 모른다(71번 §4).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.foundation.performance.domain.models import (
    AttributionSlice,
    Cashflow,
    Methodology,
    PerformanceStatement,
    ValuationSnapshot,
)


class PerformanceRepository(Protocol):
    async def get_methodology(self, version: str) -> Methodology | None: ...

    async def insert_statement(self, statement: PerformanceStatement) -> PerformanceStatement:
        """M5 `performance_statement`은 `REVOKE UPDATE, DELETE`(WORM) — append만
        가능하다. 정정은 새 리비전(state=CORRECTED)을 또 insert하는 것으로
        표현한다(correct_statement.py, L49)."""
        ...

    async def get_statement(
        self, statement_id: UUID
    ) -> PerformanceStatement | None: ...

    async def list_statements(
        self, *, tenant_id: UUID, scope: str | None = None
    ) -> tuple[PerformanceStatement, ...]: ...

    async def get_latest_statement(
        self, *, tenant_id: UUID, scope: str, scope_ref: str, period_start: datetime,
        period_end: datetime,
    ) -> PerformanceStatement | None:
        """같은 (tenant, scope, scope_ref, period)의 가장 최신 리비전 — 정정
        여부를 확인하거나 `prior_statement_id` 체인을 이을 때 쓴다."""
        ...

    async def insert_attribution(self, slice_: AttributionSlice) -> AttributionSlice:
        """`slice_.statement_id`가 대상을 가리킨다(reconciliation의
        `insert_run_with_items`처럼 부모-자식을 한 호출로 묶지 않는 이유는
        attribution이 statement 계산 이후 별도 단계(선택적 분해)이기
        때문 — L49가 실제 호출 순서를 정한다)."""
        ...

    async def list_attribution(self, statement_id: UUID) -> tuple[AttributionSlice, ...]: ...


class StatementInputPort(Protocol):
    """스코프(PAPER/LIVE)별 입력 조립 — `PaperStatementInputAdapter`(L48)가
    이 포트를 구현한다. compute_statement.py(L49)는 스코프가 뭔지 몰라도
    이 포트 하나만으로 계산에 필요한 입력을 전부 얻는다."""

    async def load_reconciled_snapshots(
        self, *, scope_ref: str, period_start: datetime, period_end: datetime
    ) -> tuple[ValuationSnapshot, ...]:
        """RESOLVED 상태가 아닌(미리컨실) 기간이 섞여 있으면 그 사실 자체를
        `ValuationSnapshot.state != RECONCILED`로 표현한다 — 조용히 걸러내지
        않는다(호출부가 판단할 수 있게)."""
        ...

    async def load_fills(
        self, *, scope_ref: str, period_start: datetime, period_end: datetime
    ) -> tuple[dict[str, object], ...]:
        """체결 원장(수수료·체결가 포함) — 구조는 어댑터가 소유(71번 §4,
        performance는 paper_control의 원시 스키마를 직접 알 필요 없다)."""
        ...

    async def load_cashflows(
        self, *, scope_ref: str, period_start: datetime, period_end: datetime
    ) -> tuple[Cashflow, ...]: ...
