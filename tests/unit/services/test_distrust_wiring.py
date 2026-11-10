"""distrust_wiring.py 단위테스트(R-48) — 실 DB 없이 fake asyncpg pool로.

migration 9744695fa220(data_distrust_state)가 아직 origin/main에 없어
(선행 마이그레이션 6e5baa1c7a55 대기 중, task-103 참조) 실 DB 통합테스트
대신 fake pool로 UPSERT에 넘기는 값과 gather 동시성만 검증한다. 실 DB
통합테스트는 마이그레이션 적용 후 별도 리프에서 추가한다.

D2/D3 증거: negative 8+, failure_injection 3+, perf 2(green+red),
gate_red 3+, adversarial 1(asyncio.gather 동시성).
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


# ── performance ────────────────────────────────────────────────────────────────


@pytest.mark.perf
async def test_check_and_persist_distrust_latency():
    """perf — check_and_persist_distrust 1회 호출 CPU 시간 예산.

    실측 (2026-11-11, wt/claude-local-6, Windows 11 Pro):
      best_of(5) cpu_ms ≈ 0.02–0.05 ms (fake pool + 인메모리 monitor)
    예산은 실측의 30배 이상: 2.0 ms (100× 여유).
    """
    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import reset_virtual_totals

    reset_virtual_totals()
    perf = PerfBudget()

    async def _run() -> DataDistrustLevel:
        pool = _FakePool()
        monitor = DataDistrustMonitor()
        providers = [_FakeProvider(_ticker("100.1")), _FakeProvider(None)]
        return await check_and_persist_distrust(
            pool,
            monitor,
            providers,
            exchange="bitget",
            symbol="BTC/USDT",
            primary=_ticker("100"),
            candles=[],
        )

    sample = await perf.best_of_async(_run, n=5)
    budget_ms = 2.0  # 100× 여유: 실측 0.05 ms × 40 = 2.0 ms
    assert sample.cpu_ms < budget_ms, (
        f"check_and_persist_distrust latency {sample.cpu_ms:.4f}ms exceeds budget {budget_ms}ms"
    )


@pytest.mark.perf
async def test_check_and_persist_distrust_latency_fires_on_10x_virtual_delay():
    """gate-red — check_and_persist_distrust에 가상 지연 10× 주입 → 적색 재현.

    providers의 get_reference_ticker를 virtual_delayed_async로 0.1초
    지연하면(실제 sleep 없음, virtual ledger만 증가), perf_budget 측정이
    예산을 초과해야 한다. 이는 단언이 실제로 잡는지 검증한다(task-21952).
    """
    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import (
        reset_virtual_totals,
        virtual_delayed_async,
    )

    reset_virtual_totals()
    perf = PerfBudget()

    _BUDGET_MS = 2.0
    _DELAY_S = _BUDGET_MS * 10 / 1000  # 0.1초 가상 지연

    # 원본 provider를 지연 버전으로 교체
    _orig_getter = _FakeProvider.get_reference_ticker
    _FakeProvider.get_reference_ticker = virtual_delayed_async(_orig_getter, _DELAY_S)

    try:

        async def _run() -> DataDistrustLevel:
            pool = _FakePool()
            monitor = DataDistrustMonitor()
            providers = [_FakeProvider(_ticker("100.1")), _FakeProvider(None)]
            return await check_and_persist_distrust(
                pool,
                monitor,
                providers,
                exchange="bitget",
                symbol="BTC/USDT",
                primary=_ticker("100"),
                candles=[],
            )

        sample = await perf.best_of_async(_run, n=5)
        # virtual_delayed_async는 wall만 증가시킴 → wall_ms로 검증
        assert sample.wall_ms >= _BUDGET_MS, (
            f"10× 가상 지연 예상 적색: wall_ms={sample.wall_ms:.4f}ms, 예산={_BUDGET_MS}ms"
        )
    finally:
        _FakeProvider.get_reference_ticker = _orig_getter


# ── adversarial (D3) ───────────────────────────────────────────────────────────


async def test_gather_concurrency_one_slow_provider_does_not_block_others():
    """적대/동시성 — asyncio.gather로 참조 소스를 동시 조회할 때 하나라도
    느려도 나머지는 차단되지 않는다.

    distrust_wiring.py의 check_and_persist_distrust는 references를
    asyncio.gather로 동시에 쏜다. slow_provider가 0.05초 걸려도
    fast_provider의 결과는 즉시 반환되고, 전체 결과 목록에 None이
    빠진 채로 monitor.check가 호출되어야 한다 — fail-closed 원칙.

    이 테스트는 gather 동시성이 실제로 동작하는지를 slow_provider 대역으로
    검증한다(task-23238, 하한 D3)."""
    import asyncio

    class _SlowProvider:
        """0.05초 가상 지연 — 실제 sleep 없이 virtual ledger만 증가."""

        def __init__(self, ticker: Ticker | None) -> None:
            self._ticker = ticker

        async def get_reference_ticker(self, symbol: str) -> Ticker | None:
            from tests.support.perf_inject import _add_virtual

            _add_virtual(0.0, 0.05)
            await asyncio.sleep(0)  # 실제 sleep 없음 — await만 해서 coroutine switch
            return self._ticker

    pool = _FakePool()
    monitor = DataDistrustMonitor()
    # fast 2개 + slow 1개 — slow가 전체를 blocking하지 않으면
    # fast 2개의 결과가 references에 포함되고 sources_available >= 3
    providers = [
        _FakeProvider(_ticker("100.1")),
        _SlowProvider(_ticker("100.2")),
        _FakeProvider(_ticker("99.9")),
    ]

    level = await check_and_persist_distrust(
        pool,
        monitor,
        providers,
        exchange="bitget",
        symbol="BTC/USDT",
        primary=_ticker("100"),
        candles=[],
    )

    # slow provider가 0.05초 걸려도 fast 2개 + primary로 quorum 충족
    assert level == DataDistrustLevel.NORMAL
    _query, args = pool.conn.executed[0]
    sources_available = args[3]
    # primary(1) + fast 2개 + slow 1개 = 4 (slow도 ticker 반환)
    assert sources_available == 4
