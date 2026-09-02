"""9.5(R-48) — tick에서 참조 시세를 모아 DataDistrustMonitor를 돌리고
결과를 영속화한다.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2.5/§9(R-48).

`check_and_persist_distrust()`가 이 모듈의 유일한 공개 진입점 — tick이
매번 호출한다. 참조 소스 조회는 서로 독립이라 `asyncio.gather`로 동시에
쏘고(하나가 느려도 나머지를 막지 않는다), 실패는 이미 각 Provider가
`None`으로 흡수했으므로 여기서 예외 처리가 필요 없다.

영속화는 (exchange, symbol) 단일 행 UPSERT — `since`는 레벨이 실제로
바뀔 때만 now()로 갱신하고, 안 바뀌면 기존 값을 그대로 유지한다(그래야
`restore_distrust_state()`가 재시작 후에도 "이 레벨이 실제로 언제부터
유지됐는지"를 정확히 복원할 수 있다 — DB 쿼리 왕복 없이 UPSERT 한
번으로 처리, 105번 조건부 쓰기와 같은 "쿼리 최소화" 원칙).
"""
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime, timezone

import asyncpg

from src.core.safety.data_distrust import DataDistrustLevel, DataDistrustMonitor
from src.data.models.market_data import Candle, Ticker
from src.services.safety.reference_quotes import ReferenceQuoteProvider


async def check_and_persist_distrust(
    pool: asyncpg.Pool,
    monitor: DataDistrustMonitor,
    providers: Sequence[ReferenceQuoteProvider],
    *,
    exchange: str,
    symbol: str,
    primary: Ticker,
    candles: list[Candle],
) -> DataDistrustLevel:
    references = list(
        await asyncio.gather(*(p.get_reference_ticker(symbol) for p in providers))
    )
    level = await monitor.check(symbol, primary, references, candles)
    sources_available = 1 + sum(1 for r in references if r is not None)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO data_distrust_state
                (exchange, symbol, level, since, sources_available, updated_at)
            VALUES ($1, $2, $3, now(), $4, now())
            ON CONFLICT (exchange, symbol) DO UPDATE SET
                since = CASE WHEN data_distrust_state.level = EXCLUDED.level
                             THEN data_distrust_state.since ELSE EXCLUDED.since END,
                level = EXCLUDED.level,
                sources_available = EXCLUDED.sources_available,
                updated_at = EXCLUDED.updated_at
            """,
            exchange,
            symbol,
            level.value,
            sources_available,
        )
    return level


async def restore_distrust_state(pool: asyncpg.Pool, monitor: DataDistrustMonitor) -> int:
    """기동 시 1회 호출 — 영속 상태 전부를 인메모리 monitor로 복원한다.
    반환값은 복원한 심볼 수(로깅·헬스체크용)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT symbol, level, since FROM data_distrust_state")

    now = datetime.now(timezone.utc)
    for row in rows:
        elapsed = max((now - row["since"]).total_seconds(), 0.0)
        monitor.restore(row["symbol"], DataDistrustLevel(row["level"]), since=elapsed)
    return len(rows)
