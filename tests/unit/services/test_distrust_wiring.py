"""distrust_wiring.py 단위테스트(R-48) — 실 DB 없이 fake asyncpg pool로.

migration 9744695fa220(data_distrust_state)가 아직 origin/main에 없어
(선행 마이그레이션 6e5baa1c7a55 대기 중, task-103 참조) 실 DB 통합테스트
대신 fake pool로 UPSERT에 넘기는 값과 gather 동시성만 검증한다. 실 DB
통합테스트는 마이그레이션 적용 후 별도 리프에서 추가한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.core.safety.data_distrust import DataDistrustLevel, DataDistrustMonitor
from src.data.models.market_data import Ticker
from src.services.safety.distrust_wiring import (
    check_and_persist_distrust,
    restore_distrust_state,
)


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


class _FakeConn:
    def __init__(self, *, fetch_rows: list[dict] | None = None) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self._fetch_rows = fetch_rows or []

    async def execute(self, query: str, *args) -> None:
        self.executed.append((query, args))

    async def fetch(self, query: str, *args):
        return self._fetch_rows


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *exc) -> None:
        return None


class _FakePool:
    def __init__(self, *, fetch_rows: list[dict] | None = None) -> None:
        self.conn = _FakeConn(fetch_rows=fetch_rows)

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self.conn)


async def test_check_and_persist_gathers_references_and_upserts_sources_available():
    pool = _FakePool()
    monitor = DataDistrustMonitor()
    providers = [_FakeProvider(_ticker("100.1")), _FakeProvider(None)]

    level = await check_and_persist_distrust(
        pool,
        monitor,
        providers,
        exchange="bitget",
        symbol="BTC/USDT",
        primary=_ticker("100"),
        candles=[],
    )

    assert level == DataDistrustLevel.NORMAL  # 참조 1개로 quorum 충족, 편차도 미미
    assert len(pool.conn.executed) == 1
    _query, args = pool.conn.executed[0]
    exchange, symbol, level_value, sources_available = args
    assert exchange == "bitget"
    assert symbol == "BTC/USDT"
    assert sources_available == 2  # primary + 참조 1개(나머지 1개는 None)


async def test_check_and_persist_with_zero_references_reports_one_source():
    pool = _FakePool()
    monitor = DataDistrustMonitor()
    providers = [_FakeProvider(None), _FakeProvider(None)]

    level = await check_and_persist_distrust(
        pool,
        monitor,
        providers,
        exchange="bitget",
        symbol="BTC/USDT",
        primary=_ticker("100"),
        candles=[],
    )

    assert level == DataDistrustLevel.DEGRADED_SINGLE_SOURCE
    _query, args = pool.conn.executed[0]
    assert args[3] == 1  # primary만


async def test_restore_distrust_state_restores_each_row_into_monitor():
    since = datetime.now(timezone.utc) - timedelta(seconds=90)
    rows = [
        {"symbol": "BTC/USDT", "level": "DISTRUSTED", "since": since},
        {"symbol": "ETH/USDT", "level": "NORMAL", "since": since},
    ]
    pool = _FakePool(fetch_rows=rows)
    monitor = DataDistrustMonitor(exit_sustain_seconds=60.0)

    count = await restore_distrust_state(pool, monitor)

    assert count == 2
    assert monitor.current_level("BTC/USDT") == DataDistrustLevel.DISTRUSTED
    assert monitor.current_level("ETH/USDT") == DataDistrustLevel.NORMAL

    # 복원된 타이머가 exit_sustain_seconds(60s)를 이미 넘겼으므로 다음
    # check()에서 바로 NORMAL로 빠져나가야 한다(90초 전부터 낮은 편차였다고
    # 복원했으므로).
    level = await monitor.check(
        "BTC/USDT", _ticker("100.1"), [_ticker("100"), _ticker("100")], []
    )
    assert level == DataDistrustLevel.NORMAL
