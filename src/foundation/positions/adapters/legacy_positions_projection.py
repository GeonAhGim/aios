"""LB-10 — `pos_snapshot`을 기존 legacy `positions` 조회 형태로 투영.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.3 LB-10.

task-376 decision: "FROZEN 아님 — 그대로 진행. ... 쓰기 경로는 건드리지
않는다 — 읽기 투영만." §2.3 표가 적어둔 `project(conn, snap)`(스냅샷 →
legacy 행 upsert)은 여기서 구현하지 않는다 — 그 쓰기 경로 전환은 LB-12
몫이다. 이 리프는 LB-9(`postgres_snapshot_repository.py`)가 이미 채우는
`pos_snapshot`을, 기존 3개 서비스(`risk_guard_service.py`·
`portfolio_service.py`·`report_service.py`)가 `positions` 테이블에서
직접 읽는 것과 같은 모양으로 읽어서 신·구 경로 조회 결과가 같음을
증명하는 읽기 전용 어댑터다.

행 대응은 `pos_snapshot.legacy_position_id`(FK `positions(id)`, LB-8)로
고정된다(§9 R10) — 이 컬럼은 아직 아무도 쓰지 않는다(LB-9 `upsert`도
채우지 않음, 위 결정 참고). `INNER JOIN`이므로 대응하는 legacy 행이 아직
연결되지 않은 스냅샷은 결과에서 조용히 빠진다(예외가 아니라 빈
리스트) — 호출자가 존재를 가정하고 예외 처리를 준비할 필요가 없다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import asyncpg

_SELECT_SQL = """
    SELECT p.id AS legacy_position_id, p.execution_id, p.strategy_id, p.symbol,
           p.exchange, ps.quantity, ps.avg_cost AS average_entry_price,
           ps.realized_pnl_base AS realized_pnl,
           COALESCE(ps.unrealized_pnl_base, 0) AS unrealized_pnl, p.closed_at
    FROM pos_snapshot ps
    JOIN positions p ON p.id = ps.legacy_position_id
    WHERE p.user_id = $1 AND p.symbol = $2 AND p.exchange = $3
    ORDER BY p.entry_time ASC
"""


@dataclass(frozen=True)
class LegacyPositionRow:
    """legacy `positions` 행 하나를 `pos_snapshot`에서 재구성한 투영 결과.

    필드는 3개 기존 서비스가 실제로 읽는 컬럼의 합집합이다 —
    `risk_guard_service`/`portfolio_service`(quantity·realized_pnl·
    unrealized_pnl 합산)와 `report_service`(strategy_id·execution_id·
    realized_pnl·closed_at, 청산 포지션만)."""

    legacy_position_id: int
    execution_id: int | None
    strategy_id: str
    symbol: str
    exchange: str
    quantity: Decimal
    average_entry_price: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    closed_at: datetime | None


class LegacyPositionsProjection:
    """`pos_snapshot`을 legacy `positions` 조회 형태로 읽는다(읽기 전용).

    어느 테이블도 갱신하지 않는다 — `project`류 쓰기 메서드는 이 클래스에
    없다(위 모듈 docstring의 결정 참고)."""

    async def get_positions(
        self,
        conn: asyncpg.Connection,
        *,
        user_id: UUID,
        symbol: str,
        exchange: str,
    ) -> list[LegacyPositionRow]:
        """같은 계정·심볼의 legacy 대응 포지션 전부(열림·청산 이력 포함,
        `entry_time` 오름차순 — legacy 재진입은 새 행). 대응하는 legacy
        행이 없으면 빈 리스트(예외 아님)."""
        rows = await conn.fetch(_SELECT_SQL, user_id, symbol, exchange)
        return [
            LegacyPositionRow(
                legacy_position_id=row["legacy_position_id"],
                execution_id=row["execution_id"],
                strategy_id=row["strategy_id"],
                symbol=row["symbol"],
                exchange=row["exchange"],
                quantity=row["quantity"],
                average_entry_price=row["average_entry_price"],
                realized_pnl=row["realized_pnl"],
                unrealized_pnl=row["unrealized_pnl"],
                closed_at=row["closed_at"],
            )
            for row in rows
        ]
