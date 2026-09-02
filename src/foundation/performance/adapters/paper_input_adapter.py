"""PAPER 스코프 `StatementInputPort` 구현.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6/§9(L48).

`scope_ref`는 이 어댑터에서 tenant의 `user_id`(문자열)로 취급한다 —
`orders`/`positions`/`strategy_executions`가 전부 `user_id`로 소유자를
표현하고(71번 §4 경계, 84b7d0faf14f 마이그레이션 편차 — P0 스콥에서
tenant_id == user_id), reconciliation(FND-08)의 `target_ref`도 같은
UUID를 가리키게 하면 별도 매핑 테이블 없이 세 컨텍스트가 같은 키로
맞물린다.

한계(명시, 스콥 축소 — 71/80/81번 여러 리프의 "아직 실제 원장이 없다"와
같은 이유):
- `orders`에는 `fee` 컬럼이 없다(210cc26533c7 마이그레이션 참조) — 체결
  수수료는 항상 알 수 없음(PENDING)으로 남는다. 0으로 채우지 않는다.
- `positions`는 현재 상태만 들고 있고 과거 시점 스냅샷을 재구성할 수
  없다(`valuation_snapshot` 테이블(M5)에 아직 아무도 쓰지 않음 — 그
  테이블은 이 리프의 스콥이 아니다). 그래서 `load_reconciled_snapshots`는
  항상 "지금" 시점(호출 시각) 스냅샷 정확히 1개만 돌려준다 — `period_start`
  시점 값을 흉내내지 않는다. TWR/MWR처럼 경계값 2개가 필요한 계산은
  compute_statement.py(L49)가 스냅샷 부족을 그대로 PENDING으로 보고해야
  한다.
- `cash`는 파생값이다: `Σstrategy_executions.allocated_capital` −
  `Σ(열린 포지션 quantity × average_entry_price)`. 실제 현금 원장이
  아니라 근사치라는 걸 호출부가 알아야 한다(그래서 위 두 한계와 함께
  `price_evidence=()`로 남겨 "가격 근거 없음"을 명시한다).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg

from src.foundation.performance.domain.models import (
    Cashflow,
    CashflowKind,
    ValuationSnapshot,
    ValuationState,
)
from src.foundation.reconciliation.contracts.v1 import Classification

PERFORMANCE_RECONCILIATION_TARGET_TYPE = "paper_account"
"""reconciliation(FND-08)에 이 컨텍스트가 쓰는 `target_type` 관례값 —
`target_ref`는 tenant의 `user_id`."""

_TRUSTED_STATUSES = frozenset({Classification.HEALTHY, Classification.RESOLVED})


class UnreconciledInputError(Exception):
    """72번 에러 taxonomy `INTEGRITY_STATEMENT_INPUT_UNRECONCILED` — 라우터가
    409로 매핑한다(L49 task 제목 "미리컨실 409"). reconciliation_state가
    아예 없거나(한 번도 리컨실 안 됨) HEALTHY/RESOLVED가 아니면(진행 중인
    불일치가 있음) 이 statement의 입력을 신뢰할 수 없다는 뜻이다."""

    def __init__(self, scope_ref: str) -> None:
        super().__init__(f"INTEGRITY_STATEMENT_INPUT_UNRECONCILED: scope_ref={scope_ref}")
        self.reason_code = "INTEGRITY_STATEMENT_INPUT_UNRECONCILED"
        self.scope_ref = scope_ref


class PaperStatementInputAdapter:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def _latest_reconciliation_run_id(
        self, conn: asyncpg.Connection, tenant_id: UUID
    ) -> UUID | None:
        row = await conn.fetchrow(
            "SELECT id FROM reconciliation_run WHERE target_ref = $1 AND target_type = $2 "
            "ORDER BY created_at DESC LIMIT 1",
            tenant_id,
            PERFORMANCE_RECONCILIATION_TARGET_TYPE,
        )
        return row["id"] if row is not None else None

    async def load_reconciled_snapshots(
        self, *, scope_ref: str, period_start: datetime, period_end: datetime
    ) -> tuple[ValuationSnapshot, ...]:
        tenant_id = UUID(scope_ref)
        async with self._pool.acquire() as conn:
            state_row = await conn.fetchrow(
                "SELECT aggregate_status FROM reconciliation_state "
                "WHERE target_ref = $1 AND target_type = $2",
                tenant_id,
                PERFORMANCE_RECONCILIATION_TARGET_TYPE,
            )
            if state_row is None or Classification(state_row["aggregate_status"]) not in (
                _TRUSTED_STATUSES
            ):
                raise UnreconciledInputError(scope_ref)

            reconciliation_run_id = await self._latest_reconciliation_run_id(conn, tenant_id)

            position_rows = await conn.fetch(
                "SELECT p.symbol, p.exchange, p.quantity, p.average_entry_price, "
                "       p.unrealized_pnl, p.realized_pnl "
                "FROM positions p JOIN strategy_executions e ON e.id = p.execution_id "
                "WHERE p.user_id = $1 AND e.mode = 'PAPER' AND p.entry_time <= $3 "
                "AND (p.closed_at IS NULL OR p.closed_at >= $2)",
                tenant_id,
                period_start,
                period_end,
            )
            capital_row = await conn.fetchrow(
                "SELECT COALESCE(SUM(allocated_capital), 0) AS total FROM strategy_executions "
                "WHERE user_id = $1 AND mode = 'PAPER' AND created_at <= $2",
                tenant_id,
                period_end,
            )

        positions = tuple(
            {
                "symbol": r["symbol"],
                "exchange": r["exchange"],
                "quantity": str(r["quantity"]),
                "average_entry_price": str(r["average_entry_price"]),
                "unrealized_pnl": str(r["unrealized_pnl"]),
                "realized_pnl": str(r["realized_pnl"]),
            }
            for r in position_rows
        )
        deployed_notional = sum(
            (r["quantity"] * r["average_entry_price"] for r in position_rows), Decimal(0)
        )
        cash = capital_row["total"] - deployed_notional

        snapshot = ValuationSnapshot(
            id=uuid4(),
            tenant_id=tenant_id,
            scope="PAPER",
            scope_ref=scope_ref,
            as_of=period_end,
            positions=positions,
            cash=cash,
            price_evidence=(),
            reconciliation_run_id=reconciliation_run_id,
            state=ValuationState.RECONCILED,
        )
        return (snapshot,)

    async def load_fills(
        self, *, scope_ref: str, period_start: datetime, period_end: datetime
    ) -> tuple[dict[str, object], ...]:
        tenant_id = UUID(scope_ref)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT o.order_id, o.symbol, o.exchange, o.side, o.filled_quantity, "
                "       o.average_fill_price, o.updated_at "
                "FROM orders o JOIN strategy_executions e ON e.id = o.execution_id "
                "WHERE o.user_id = $1 AND e.mode = 'PAPER' AND o.status = 'FILLED' "
                "AND o.updated_at >= $2 AND o.updated_at <= $3",
                tenant_id,
                period_start,
                period_end,
            )
        return tuple(
            {
                "order_id": str(r["order_id"]),
                "symbol": r["symbol"],
                "exchange": r["exchange"],
                "side": r["side"],
                "filled_quantity": str(r["filled_quantity"]),
                "average_fill_price": (
                    str(r["average_fill_price"]) if r["average_fill_price"] is not None else None
                ),
                "fee": None,  # orders에 fee 컬럼 없음 — 항상 PENDING(위 모듈 docstring 참조)
                "at": r["updated_at"].isoformat(),
            }
            for r in rows
        )

    async def load_cashflows(
        self, *, scope_ref: str, period_start: datetime, period_end: datetime
    ) -> tuple[Cashflow, ...]:
        tenant_id = UUID(scope_ref)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT allocated_capital, started_at FROM strategy_executions "
                "WHERE user_id = $1 AND mode = 'PAPER' AND started_at IS NOT NULL "
                "AND started_at >= $2 AND started_at <= $3",
                tenant_id,
                period_start,
                period_end,
            )
        return tuple(
            Cashflow(at=r["started_at"], amount=r["allocated_capital"], kind=CashflowKind.DEPOSIT)
            for r in rows
        )
