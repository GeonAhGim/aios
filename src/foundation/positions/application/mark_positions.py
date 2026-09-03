"""LB-14 — 열린 스냅샷에 마크가격·FX를 적용해 미실현 PnL을 갱신
(application/mark_positions.py).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §9.3 LB-14.

MARK은 저널행을 남기지 않는 파생값 갱신이다(§4.3 "스냅샷 = fold(저널)"과
별개 — `pos_journal`은 건드리지 않고 `pos_snapshot.mark_price/mark_at/
unrealized_pnl_base`만 조건부 upsert한다). 마크가 없거나 스테일하면
(`marks.mark`가 `None`) `mark_price`/`mark_at`/`unrealized_pnl_base`를
전부 `None`으로 덮어쓴다 — 직전 값을 그대로 둔 채 갱신만 건너뛰면
호출부가 오래된 마크를 최신인 것처럼 오인하는 조용한 오평가가 된다
(task-654 decision, "직전값으로 채우는 fallback 금지").

`domain/pnl.unrealized`는 `mark.currency`가 `snapshot.avg_cost.currency`와
다르면 `CurrencyMismatchError`로 즉시 실패한다(그 상황은 FX로 메울 수
없는 마크 소스 배선 버그다 — `MarkPriceSource`는 항상 포지션의 원가
통화로 마크를 돌려줘야 한다는 전제, `record_fill`이 체결가 통화를
검증하지 않는 것과 같은 신뢰 경계). 이 함수가 실제로 FX를 조회하는
경우는 그 전제가 이미 성립한 뒤 `avg_cost.currency`(=`mark.currency`)가
`base_currency`와 다를 때뿐이다 — `pnl.unrealized`가 그 차이만 환산에
쓴다. FX가 필요한데 없으면(`fx.FxRateMissingError`/`FxRateStaleError`)
마크·시각은 그대로 기록하되 `unrealized_pnl_base`만 `None`으로 남긴다
(§3.2 taxonomy "POS_FX_RATE_MISSING → 해당 계좌 미실현 None/스킵, 0
금지").

동시 체결로 `last_journal_seq`가 그 사이 바뀌면
`SnapshotRepository.upsert`가 `ConcurrencyConflictError`를 던진다 — 그
포트의 계약("이 예외를 삼키지 않는다")대로 여기서도 삼키지 않고 그대로
전파한다(재시도 정책은 스케줄러 LB-17의 몫, 이 리프 범위 밖).

`contract_multiplier`는 스냅샷에 저장되지 않는다(`record_fill.py`와 같은
제약, `RecordFillCommand`에만 있고 `pos_snapshot`엔 저장할 곳이 없다) —
Phase 1은 현물만 다루므로(R6, 파생상품 숏 포지션은 테스트 전용) 이
리프는 `pnl.unrealized`의 기본값(1)을 그대로 쓴다.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

import asyncpg

from src.data.models.base import FXRate
from src.foundation.positions.contracts.v1 import PositionSnapshotView
from src.foundation.positions.domain import pnl
from src.foundation.positions.domain.fx import FxRateMissingError
from src.foundation.positions.ports.fx_rate_source import FxRateSource
from src.foundation.positions.ports.mark_price_source import MarkPriceSource
from src.foundation.positions.ports.snapshot_repository import SnapshotRepository

Clock = Callable[[], datetime]

__all__ = ["mark_positions"]


async def mark_positions(
    tenant_id: UUID,
    account_id: UUID,
    *,
    snapshots: SnapshotRepository,
    marks: MarkPriceSource,
    fx: FxRateSource,
    pool: asyncpg.Pool,
    clock: Clock,
) -> list[PositionSnapshotView]:
    now = clock()
    async with pool.acquire() as conn:
        open_positions = await snapshots.list_open(conn, tenant_id, account_id)

    updated: list[PositionSnapshotView] = []
    for snapshot in open_positions:
        mark = await marks.mark(snapshot.position_key, now)
        if mark is None:
            fields: dict[str, object] = {
                "mark_price": None,
                "mark_at": None,
                "unrealized_pnl_base": None,
            }
        else:
            rate: FXRate | None = None
            if snapshot.avg_cost.currency != snapshot.base_currency:
                rate = await fx.rate(snapshot.avg_cost.currency, snapshot.base_currency, now)
            try:
                unrealized = pnl.unrealized(snapshot, mark, rate, now=now).unrealized
            except FxRateMissingError:
                unrealized = None
            fields = {"mark_price": mark, "mark_at": now, "unrealized_pnl_base": unrealized}

        new_snapshot = snapshot.model_copy(update=fields)
        async with pool.acquire() as conn:
            persisted = await snapshots.upsert(
                conn, new_snapshot, expected_seq=snapshot.last_journal_seq
            )
        updated.append(persisted)

    return updated
