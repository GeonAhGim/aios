"""distrust_wiring.py 실 DB 통합테스트(R-48) — data_distrust_state
테이블(9744695fa220) 대상. 단위테스트(tests/unit/services/
test_distrust_wiring.py)는 fake pool로 UPSERT 인자/gather만 검증하고,
여기서는 실제 UPSERT의 since 보존·갱신 규칙과 restore 왕복을 검증한다.

DEEPEN(task-9398): negative test ≥3, failure-injection ≥1 추가.
"""

import asyncio
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


async def test_two_scheduler_instances_racing_the_same_symbol_leave_one_coherent_row(pool):
    """다중 인스턴스 증명(D3) — 실제 배포에서 두 프로세스가 각자 별도
    `DataDistrustMonitor` 인스턴스로 같은 (exchange, symbol)을 동시에
    관측·영속화할 수 있다(watchdog 재시작 겹침, 배포 중 신구 프로세스
    공존 등). ON CONFLICT UPSERT가 105번 표준대로 안전한지 실제 asyncio
    동시 실행으로 증명한다 — 데드락/예외 없이 정확히 한 행만 남아야 하고,
    그 행의 level은 두 인스턴스 중 하나가 실제로 계산한 값과 일치해야
    한다(값이 섞여 CHECK 제약을 벗어난 제3의 값이 되지 않는다)."""
    symbol = f"DIST-{uuid.uuid4().hex[:8]}/USDT"
    monitor_a = DataDistrustMonitor()  # "프로세스 A"
    monitor_b = DataDistrustMonitor()  # "프로세스 B" — 독립 인메모리 상태

    async def _tick(monitor: DataDistrustMonitor) -> DataDistrustLevel:
        providers = [_FakeProvider(_ticker("150")), _FakeProvider(_ticker("151"))]
        return await check_and_persist_distrust(
            pool,
            monitor,
            providers,
            exchange="bitget",
            symbol=symbol,
            primary=_ticker("100"),
            candles=[],
        )

    results = await asyncio.gather(_tick(monitor_a), _tick(monitor_b))

    assert set(results) == {DataDistrustLevel.DISTRUSTED}  # 둘 다 같은 입력 -> 같은 판정
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT level FROM data_distrust_state WHERE exchange = 'bitget' AND symbol = $1",
            symbol,
        )
    assert len(rows) == 1  # PK(exchange, symbol) 하나 -> 경합 후에도 행 하나
    assert rows[0]["level"] == DataDistrustLevel.DISTRUSTED.value


# ── negative tests (task-9398 DEEPEN) ──────────────────────────────────


async def test_zero_price_in_primary_ticker_does_not_crash_statistical_check(pool):
    """negative 1: primary price=0 → 괴리 계산에서 0/0 또는 x/0 발생 가능.
    `_statistical_check`는 last_close==0에서 True(skip)하므로 NORMAL/DEGRADED
    로 떨어지고 DB에 정상 쓰여야 한다(예외가 아닌 safe default)."""
    symbol = f"DIST-{uuid.uuid4().hex[:8]}/USDT"
    monitor = DataDistrustMonitor()
    providers = [_FakeProvider(None), _FakeProvider(None)]

    zero_price_ticker = _ticker("0")
    # zero_price_ticker의 price/bid/ask가 모두 0이므로 _statistical_check에서
    # move_pct == 0 조건만 통과하면 safe default.

    level = await check_and_persist_distrust(
        pool,
        monitor,
        providers,
        exchange="bitget",
        symbol=symbol,
        primary=zero_price_ticker,
        candles=[],
    )

    # 0원 ticker → 통계 검사 skip → references 0개이므로 DEGRADED_SINGLE_SOURCE
    assert level == DataDistrustLevel.DEGRADED_SINGLE_SOURCE
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT level, sources_available FROM data_distrust_state "
            "WHERE exchange = 'bitget' AND symbol = $1",
            symbol,
        )
    assert row is not None
    assert row["sources_available"] == 1


async def test_corrupted_level_in_db_raises_on_restore(pool):
    """negative 2: DB에 enum에 없는 level 문자열이 들어있으면
    DataDistrustLevel() 생성 시 ValueError가 난다.
    마이그레이션 CHECK 제약(data_distrust_state_level_check)이
    INSERT 시점을 막으므로, 이 테스트는 CHECK 제약이 실제로
    무효 값을 차단하는지 검증한다."""
    symbol = f"DIST-{uuid.uuid4().hex[:8]}/USDT"
    # enum에 없는 값을 직접 삽입 시도 — CHECK 제약이 CheckViolationError를 던져야 함
    async with pool.acquire() as conn:
        with pytest.raises(Exception, match=".*"):  # CheckViolationError (CHECK 제약 위반)
            await conn.execute(
                "INSERT INTO data_distrust_state "
                "(exchange, symbol, level, since, sources_available, updated_at) "
                "VALUES ($1, $2, $3, now(), 0, now()) ON CONFLICT DO NOTHING",
                "bitget",
                symbol,
                "INVALID_LEVEL_GARBAGE",
            )
        # DB에 행이 쓰이지 않았음을 확인 (동일 connection 안에서)
        row = await conn.fetchrow(
            "SELECT 1 FROM data_distrust_state WHERE exchange = 'bitget' AND symbol = $1",
            symbol,
        )
        assert row is None


async def test_all_providers_return_none_yields_degraded_not_crash(pool):
    """negative 3: 모든 참조 소스가 None을 반환하면 references가
    비어 있고 primary만 있으므로 DEGRADED_SINGLE_SOURCE가 된다.
    예외가 아니라 safe default 수준으로 떨어진다."""
    symbol = f"DIST-{uuid.uuid4().hex[:8]}/USDT"
    monitor = DataDistrustMonitor()
    # 모든 provider가 None 반환
    providers = [_FakeProvider(None), _FakeProvider(None)]

    level = await check_and_persist_distrust(
        pool,
        monitor,
        providers,
        exchange="bitget",
        symbol=symbol,
        primary=_ticker("100"),
        candles=[],
    )

    assert level == DataDistrustLevel.DEGRADED_SINGLE_SOURCE
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT level, sources_available FROM data_distrust_state "
            "WHERE exchange = 'bitget' AND symbol = $1",
            symbol,
        )
    assert row is not None
    assert row["level"] == DataDistrustLevel.DEGRADED_SINGLE_SOURCE.value
    assert row["sources_available"] == 1


# ── failure-injection test ─────────────────────────────────────────────


async def test_provider_timeout_during_gather_propagates_and_prevents_persist(pool):
    """실패주입 1: 참조 소스 어댑터가 타임아웃으로 예외를 내면
    asyncio.gather가 실패하고 check_and_persist_distrust가 예외 전파.
    DB에 잘못된 상태(NORMAL/DEGRADED)가 쓰이지 않음을 실 DB로 검증."""
    symbol = f"DIST-{uuid.uuid4().hex[:8]}/USDT"
    monitor = DataDistrustMonitor()

    class _TimeoutProvider:
        async def get_reference_ticker(self, symbol: str) -> Ticker | None:
            await asyncio.sleep(10)  # 영원히BLOCK
            return None

    providers = [_FakeProvider(_ticker("100.1")), _TimeoutProvider()]

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            check_and_persist_distrust(
                pool,
                monitor,
                providers,
                exchange="bitget",
                symbol=symbol,
                primary=_ticker("100"),
                candles=[],
            ),
            timeout=2,
        )

    # DB에 행이 쓰이지 않았음을 실측
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM data_distrust_state WHERE exchange = 'bitget' AND symbol = $1",
            symbol,
        )
    assert row is None  # 예외 전파 → UPSERT 미실행 → DB에 행 없음
