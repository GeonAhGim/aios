"""distrust_wiring.py 실 DB 통합테스트(R-48) — data_distrust_state
테이블(9744695fa220) 대상. 단위테스트(tests/unit/services/
test_distrust_wiring.py)는 fake pool로 UPSERT 인자/gather만 검증하고,
여기서는 실제 UPSERT의 since 보존·갱신 규칙과 restore 왕복을 검증한다.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.safety.data_distrust import DataDistrustLevel, DataDistrustMonitor
from src.data.models.market_data import Ticker
from src.services.safety.distrust_wiring import (
    check_and_persist_distrust,
    restore_distrust_state,
)


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


def _ticker(price: str) -> Ticker:
    return Ticker(
        symbol="BTC/USDT",
        exchange="bitget",
        price=Decimal(price),
        bid=Decimal(price),
        ask=Decimal(price),
        volume_24h=Decimal("1"),
        timestamp=datetime.now(timezone.utc),
        source_type="primary",
    )


class _FakeProvider:
    def __init__(self, ticker: Ticker | None) -> None:
        self._ticker = ticker

    async def get_reference_ticker(self, symbol: str) -> Ticker | None:
        return self._ticker


async def test_upsert_preserves_since_when_level_unchanged(pool):
    # 격리를 위해 매 테스트 유일한 심볼을 쓴다 — 공유 dev/test DB의 다른
    # 세션 데이터와 (exchange, symbol) PK가 충돌하지 않는다.
    symbol = f"DIST-{uuid.uuid4().hex[:8]}/USDT"
    monitor = DataDistrustMonitor()
    providers = [_FakeProvider(None), _FakeProvider(None)]  # 참조 0개 -> DEGRADED

    async def _record() -> DataDistrustLevel:
        return await check_and_persist_distrust(
            pool,
            monitor,
            providers,
            exchange="bitget",
            symbol=symbol,
            primary=_ticker("100"),
            candles=[],
        )

    await _record()
    async with pool.acquire() as conn:
        first_since = await conn.fetchval(
            "SELECT since FROM data_distrust_state WHERE exchange = 'bitget' AND symbol = $1",
            symbol,
        )
    assert first_since is not None

    # 같은 레벨로 다시 기록 — since가 그대로 유지돼야 한다(레벨 불변).
    await _record()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT level, since, sources_available FROM data_distrust_state "
            "WHERE exchange = 'bitget' AND symbol = $1",
            symbol,
        )
    assert row["level"] == DataDistrustLevel.DEGRADED_SINGLE_SOURCE.value
    assert row["since"] == first_since
    assert row["sources_available"] == 1


async def test_restore_distrust_state_round_trips_through_real_table(pool):
    symbol = f"DIST-{uuid.uuid4().hex[:8]}/USDT"
    seed_monitor = DataDistrustMonitor()
    # 큰 괴리(150/151 vs primary 100) -> DISTRUSTED
    providers = [_FakeProvider(_ticker("150")), _FakeProvider(_ticker("151"))]

    level = await check_and_persist_distrust(
        pool,
        seed_monitor,
        providers,
        exchange="bitget",
        symbol=symbol,
        primary=_ticker("100"),
        candles=[],
    )
    assert level == DataDistrustLevel.DISTRUSTED

    fresh_monitor = DataDistrustMonitor()
    assert fresh_monitor.current_level(symbol) == DataDistrustLevel.NORMAL  # 복원 전
    await restore_distrust_state(pool, fresh_monitor)
    assert fresh_monitor.current_level(symbol) == DataDistrustLevel.DISTRUSTED
