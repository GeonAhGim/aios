"""distrust_wiring.py 단위테스트(R-48) — 실 DB 없이 fake asyncpg pool로.

migration 9744695fa220(data_distrust_state)가 아직 origin/main에 없어
(선행 마이그레이션 6e5baa1c7a55 대기 중, task-103 참조) 실 DB 통합테스트
대신 fake pool로 UPSERT에 넘기는 값과 gather 동시성만 검증한다. 실 DB
통합테스트는 마이그레이션 적용 후 별도 리프에서 추가한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.core.safety.data_distrust import DataDistrustLevel, DataDistrustMonitor
from src.data.models.market_data import Ticker
from src.services.safety.distrust_wiring import (
    check_and_persist_distrust,
    restore_distrust_state,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync


def _ticker(price: str) -> Ticker:
    return Ticker(
        symbol="BTC/USDT",
        exchange="bitget",
        price=Decimal(price),
        bid=Decimal(price),
        ask=Decimal(price),
        volume_24h=Decimal("1"),
        timestamp=datetime.now(UTC),
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


async def test_negative_zero_references_with_empty_candles_still_upserts_once():
    """negative — 참조도 candle도 모두 빈 상태에서도 UPSERT는 정확히 1회(중복/누락 없음)."""
    pool = _FakePool()
    level = await check_and_persist_distrust(
        pool,
        DataDistrustMonitor(),
        [_FakeProvider(None)],
        exchange="bitget",
        symbol="ETH/USDT",
        primary=_ticker("2000"),
        candles=[],
    )
    assert level == DataDistrustLevel.DEGRADED_SINGLE_SOURCE
    assert len(pool.conn.executed) == 1
    _query, args = pool.conn.executed[0]
    assert args[1] == "ETH/USDT"
    assert args[3] == 1


async def test_negative_zero_references_different_symbols_do_not_merge():
    """negative — 서로 다른 심볼에 zero references를 돌려도 DB 행이 합쳐지지 않는다."""
    pool = _FakePool()
    monitor = DataDistrustMonitor()
    # ETH: 참조 0개, BTC: 참조 0개 — 서로 다른 monitor 없이 같은 monitor로 동시 실행
    await check_and_persist_distrust(
        pool,
        monitor,
        [_FakeProvider(None)],
        exchange="bitget",
        symbol="ETH/USDT",
        primary=_ticker("2000"),
        candles=[],
    )
    await check_and_persist_distrust(
        pool,
        monitor,
        [_FakeProvider(None)],
        exchange="bitget",
        symbol="SOL/USDT",
        primary=_ticker("100"),
        candles=[],
    )
    assert len(pool.conn.executed) == 2
    symbols = {args[1] for _q, args in pool.conn.executed}
    assert symbols == {"ETH/USDT", "SOL/USDT"}
    assert all(args[3] == 1 for _q, args in pool.conn.executed)


async def test_negative_single_provider_returns_none_same_as_zero_providers():
    """negative — 참조 provider가 1개이고 None을 반환하는 것, provider가 0개인 것은
    sources_available가 모두 1(primary only)로 동일하게 DEGRADED_SINGLE_SOURCE여야 한다."""
    pool_1 = _FakePool()
    pool_0 = _FakePool()
    level_1 = await check_and_persist_distrust(
        pool_1,
        DataDistrustMonitor(),
        [_FakeProvider(None)],  # provider 1개, None 반환
        exchange="bitget",
        symbol="BTC/USDT",
        primary=_ticker("100"),
        candles=[],
    )
    level_0 = await check_and_persist_distrust(
        pool_0,
        DataDistrustMonitor(),
        [],  # provider 0개
        exchange="bitget",
        symbol="BTC/USDT",
        primary=_ticker("100"),
        candles=[],
    )
    assert level_1 == level_0 == DataDistrustLevel.DEGRADED_SINGLE_SOURCE
    assert pool_1.conn.executed[0][1][3] == 1
    assert pool_0.conn.executed[0][1][3] == 1


class _RaisingProvider:
    """계약 위반 대역 — reference_quotes.py의 두 구현체는 실패를 항상 None
    으로 흡수하지만(모듈 docstring), 미래의 세 번째 구현이 그 계약을 어길
    가능성에 대비해 check_and_persist_distrust가 이를 삼키지 않고 그대로
    전파하는지(fail-closed) 검증한다 — 조용히 NORMAL/DEGRADED로 잘못
    판정하고 DB에 쓰는 것보다 예외로 이번 틱을 실패시키는 편이 안전하다."""

    async def get_reference_ticker(self, symbol: str) -> Ticker | None:
        raise RuntimeError("참조 소스 어댑터 내부 결함(계약 위반 시뮬레이션)")


async def test_provider_exception_propagates_instead_of_silently_persisting():
    pool = _FakePool()
    monitor = DataDistrustMonitor()
    providers = [_FakeProvider(_ticker("100.1")), _RaisingProvider()]

    with pytest.raises(RuntimeError):
        await check_and_persist_distrust(
            pool,
            monitor,
            providers,
            exchange="bitget",
            symbol="BTC/USDT",
            primary=_ticker("100"),
            candles=[],
        )

    assert pool.conn.executed == []  # 예외 발생 전이라 잘못된 상태가 DB에 쓰이지 않음


async def test_restore_distrust_state_restores_each_row_into_monitor():
    since = datetime.now(UTC) - timedelta(seconds=90)
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
    level = await monitor.check("BTC/USDT", _ticker("100.1"), [_ticker("100"), _ticker("100")], [])
    assert level == DataDistrustLevel.NORMAL


async def test_restore_distrust_state_rejects_unknown_level_value():
    """DB 행에 DataDistrustLevel enum에 없는 값이 들어있으면(스키마 드리프트,
    수동 DML 오염 등 불변식 위반) 조용히 무시하거나 임의 기본값으로 복원하지
    않고 ValueError로 즉시 실패해야 한다 — fail-closed."""
    since = datetime.now(UTC) - timedelta(seconds=10)
    rows = [{"symbol": "BTC/USDT", "level": "NOT_A_REAL_LEVEL", "since": since}]
    pool = _FakePool(fetch_rows=rows)
    monitor = DataDistrustMonitor()

    with pytest.raises(ValueError):
        await restore_distrust_state(pool, monitor)


async def test_restore_distrust_state_rejects_malformed_row_missing_since():
    """행에 `since` 컬럼이 없는 경우(마이그레이션 불일치 등) KeyError를 그대로
    전파해야 한다 — 타임스탬프를 임의로 0/now로 대체해 잘못된 경과시간을
    조용히 복원하면 exit_sustain_seconds 판정이 틀어진다."""
    rows = [{"symbol": "BTC/USDT", "level": "NORMAL"}]  # since 누락
    pool = _FakePool(fetch_rows=rows)
    monitor = DataDistrustMonitor()

    with pytest.raises(KeyError):
        await restore_distrust_state(pool, monitor)


class _FailingExecuteConn(_FakeConn):
    async def execute(self, query: str, *args) -> None:
        raise ConnectionError("DB 커넥션 끊김(장애 주입)")


async def test_check_and_persist_propagates_db_write_failure():
    """실패주입: UPSERT 도중 DB 커넥션이 끊기면 예외를 삼켜 레벨만 반환하고
    끝내는 대신(다음 틱이 상태 불일치를 못 알아챔) 그대로 전파해야 한다."""
    pool = _FakePool()
    pool.conn = _FailingExecuteConn()
    monitor = DataDistrustMonitor()
    providers = [_FakeProvider(_ticker("100.1"))]

    with pytest.raises(ConnectionError):
        await check_and_persist_distrust(
            pool,
            monitor,
            providers,
            exchange="bitget",
            symbol="BTC/USDT",
            primary=_ticker("100"),
            candles=[],
        )


# ---------------------------------------------------------------------------
# DEEPEN-DEPTH R-48 깊이 하한 보강 D1->D3 (task-23226)
# ---------------------------------------------------------------------------

# 신설 예산: 로컬 실측(2026-11-11, best_of n=10 batch=8) cpu_ms≈0.0(Windows process_time
# 15.6ms 양자화 미만). 양자화 1틱 근처인 15ms를 예산으로 두면 실측 대비 30배 이상 여유이고,
# 가상 지연 10배(150ms) 주입 시 적색이 된다. 기존 단언·예산은 건드리지 않는다.
_DISTRUST_CPU_BUDGET_MS = 15.0


async def _best_cpu_ms(providers: list) -> float:
    async def work() -> None:
        await check_and_persist_distrust(
            _FakePool(),
            DataDistrustMonitor(),
            providers,
            exchange="bitget",
            symbol="BTC/USDT",
            primary=_ticker("100"),
            candles=[],
        )

    return (await PerfBudget().best_of_async(work, n=10, batch=8)).cpu_ms


@pytest.mark.perf
async def test_check_and_persist_best_of_cpu_within_budget():
    """성능 단언 — best-of-10 단일 check_and_persist CPU 시간이 신설 예산 미만."""
    reset_virtual_totals()
    cpu_ms = await _best_cpu_ms([_FakeProvider(_ticker("100.1")), _FakeProvider(None)])
    assert cpu_ms < _DISTRUST_CPU_BUDGET_MS, f"cpu_ms={cpu_ms:.3f} >= {_DISTRUST_CPU_BUDGET_MS}"


class _VirtualSlowProvider(_FakeProvider):
    async def get_reference_ticker(self, symbol: str) -> Ticker | None:
        virtual_delayed_sync(lambda: None, _DISTRUST_CPU_BUDGET_MS * 10 / 1000)()
        return await super().get_reference_ticker(symbol)


@pytest.mark.perf
async def test_check_and_persist_cpu_budget_red_with_virtual_delay_10x():
    """게이트 적색 재현 — 참조 소스에 가상 지연(예산 10배)을 주입하면 위 단언이 적색."""
    reset_virtual_totals()
    try:
        cpu_ms = await _best_cpu_ms([_VirtualSlowProvider(_ticker("100.1"))])
    finally:
        reset_virtual_totals()
    with pytest.raises(AssertionError):
        assert cpu_ms < _DISTRUST_CPU_BUDGET_MS


async def test_negative_no_providers_reports_single_source_degraded():
    """negative — 참조 소스가 하나도 없으면 NORMAL이 아니라 단일 소스 강등."""
    pool = _FakePool()
    level = await check_and_persist_distrust(
        pool,
        DataDistrustMonitor(),
        [],
        exchange="bitget",
        symbol="BTC/USDT",
        primary=_ticker("100"),
        candles=[],
    )
    assert level == DataDistrustLevel.DEGRADED_SINGLE_SOURCE
    assert pool.conn.executed[0][1][3] == 1


async def test_negative_large_deviation_is_not_normal():
    """negative — 참조가 primary와 크게 어긋나면 NORMAL로 판정하지 않는다."""
    pool = _FakePool()
    level = await check_and_persist_distrust(
        pool,
        DataDistrustMonitor(),
        [_FakeProvider(_ticker("150")), _FakeProvider(_ticker("150"))],
        exchange="bitget",
        symbol="BTC/USDT",
        primary=_ticker("100"),
        candles=[],
    )
    assert level != DataDistrustLevel.NORMAL
    assert pool.conn.executed[0][1][2] == level.value


async def test_negative_all_providers_none_still_upserts_exactly_once():
    """negative — 참조가 전부 None이어도 UPSERT는 정확히 1회(중복/누락 없음)."""
    pool = _FakePool()
    await check_and_persist_distrust(
        pool,
        DataDistrustMonitor(),
        [_FakeProvider(None)] * 3,
        exchange="bitget",
        symbol="BTC/USDT",
        primary=_ticker("100"),
        candles=[],
    )
    assert len(pool.conn.executed) == 1


async def test_adversarial_concurrent_symbols_do_not_cross_contaminate_and_replay_is_stable():
    """동시성+리플레이 — 같은 monitor를 공유해 서로 다른 심볼 20건을 동시에 돌려도
    각 UPSERT는 자기 심볼만 담고, 같은 입력 재실행은 동일 인자를 낸다."""
    import asyncio

    monitor = DataDistrustMonitor()
    pool = _FakePool()
    symbols = [f"S{i}/USDT" for i in range(20)]

    async def run(sym: str) -> DataDistrustLevel:
        return await check_and_persist_distrust(
            pool,
            monitor,
            [_FakeProvider(_ticker("100.1"))],
            exchange="bitget",
            symbol=sym,
            primary=_ticker("100"),
            candles=[],
        )

    levels = await asyncio.gather(*(run(s) for s in symbols))
    assert set(levels) == {DataDistrustLevel.NORMAL}
    persisted = sorted(args[1] for _q, args in pool.conn.executed)
    assert persisted == sorted(symbols)
    assert all(args[3] == 2 for _q, args in pool.conn.executed)

    replay_pool = _FakePool()
    replay_args = []
    for _ in range(2):
        await check_and_persist_distrust(
            replay_pool,
            DataDistrustMonitor(),
            [_FakeProvider(_ticker("100.1"))],
            exchange="bitget",
            symbol="BTC/USDT",
            primary=_ticker("100"),
            candles=[],
        )
        replay_args.append(replay_pool.conn.executed[-1][1])
    assert replay_args[0] == replay_args[1]
