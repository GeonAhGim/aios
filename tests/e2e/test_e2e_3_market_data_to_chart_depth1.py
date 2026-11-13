"""E2E-3 depth 보강(D1->D2) — 시세 적재→차트 집계 경로의 negative/실패주입/게이트 적색/성능.

기존 `test_e2e_3_market_data_to_chart.py`는 수정하지 않고, 부족한 증거만 보조 파일로 더한다.

- negative 3종: rollup 타깃 M1 거부, 정렬 위반 거부, 컬럼 길이 불일치 거부 (DC-10 fail-closed).
- 실패 주입: ingest source HTTP 500 예외가 run_ingest 밖으로 전파된다.
- 게이트 적색: 집계 입력(high)을 망가뜨리면 OHLCV 동일성 단언이 실제로 적색이 된다.
- 성능: 300 M1 -> M5 rollup best-of-5 cpu 실측 약 2ms(로컬), 예산 200ms(30배 이상 여유).
  10배 가상 지연(perf_inject)을 주입하면 같은 예산 단언이 적색이 된다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import asyncpg
import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.market_data.adapters.postgres_batch_repository import (
    PostgresBatchRepository,
)
from src.foundation.market_data.adapters.postgres_calendar_repository import (
    PostgresCalendarRepository,
)
from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
from src.foundation.market_data.adapters.postgres_reference_repository import (
    PostgresReferenceRepository,
)
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.domain.aggregation.timeframe_rollup import (
    InvalidRollupTargetError,
    UnsortedCandlesError,
    rollup,
)
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.candle_columns import CandleColumns
from tests.e2e.market_data_helpers import (
    MockIngestSource,
    ingest_cmd,
    listed_instrument,
    run_ingest,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

_BASE = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
_ROLLUP_BUDGET_MS = 200.0  # 실측 약 2ms의 30배 이상 여유


def _calendar() -> VenueCalendar:
    session = KNOWN_SESSIONS[Venue.BITGET.value]
    return VenueCalendar(venue=Venue.BITGET.value, tz=session.tz, regular=session)


def _columns(n: int, *, high_bump: Decimal = Decimal("0")) -> CandleColumns:
    ts = [_BASE + timedelta(minutes=i) for i in range(n)]
    return CandleColumns(
        ts=ts,
        open=[Decimal("100")] * n,
        high=[Decimal("110") + high_bump] * n,
        low=[Decimal("90")] * n,
        close=[Decimal("105")] * n,
        volume=[Decimal("10")] * n,
        quote_volume=[Decimal("1000")] * n,
    )


def test_rollup_rejects_m1_target() -> None:
    """negative: M1 -> M1 집계는 InvalidRollupTargetError로 거부된다."""
    with pytest.raises(InvalidRollupTargetError):
        rollup(_columns(5), Timeframe.M1, _calendar())


def test_rollup_rejects_unsorted_input() -> None:
    """negative: open_time 역순 입력은 조용히 집계되지 않고 UnsortedCandlesError."""
    cols = _columns(5)
    cols.ts[1], cols.ts[2] = cols.ts[2], cols.ts[1]
    with pytest.raises(UnsortedCandlesError):
        rollup(cols, Timeframe.M5, _calendar())


def test_rollup_rejects_mismatched_column_lengths() -> None:
    """negative: 컬럼 길이가 다르면 부분 집계 대신 예외(fail-closed)."""
    cols = _columns(5)
    cols.close.pop()
    with pytest.raises(ValueError):
        rollup(cols, Timeframe.M5, _calendar())


def test_gate_red_corrupted_high_breaks_ohlcv_identity() -> None:
    """게이트 적색 재현: high를 망가뜨린 입력이면 M5 high 동일성 단언이 실제로 적색이 된다."""
    good = rollup(_columns(5), Timeframe.M5, _calendar()).columns
    bad = rollup(_columns(5, high_bump=Decimal("7")), Timeframe.M5, _calendar()).columns
    assert good.high[0] == Decimal("110")  # 정상 입력은 단언 통과
    with pytest.raises(AssertionError):
        assert bad.high[0] == Decimal("110"), "M5 high = max(M1 high)"
    assert bad.high[0] == Decimal("117")


@pytest.fixture
def deps(pool: asyncpg.Pool) -> SimpleNamespace:
    return SimpleNamespace(
        pool=pool,
        refs=PostgresReferenceRepository(pool),
        cal=PostgresCalendarRepository(pool),
        audit=PostgresAuditEventRepository(pool),
        store=PostgresCandleStore(pool),
        batches=PostgresBatchRepository(pool),
    )


async def test_failure_injection_source_http_error_propagates(deps: SimpleNamespace) -> None:
    """실패 주입: 외부 source HTTP 500은 삼켜지지 않고 호출자에게 전파된다."""
    instrument = await listed_instrument(deps, Venue.BITGET, _BASE)
    source = MockIngestSource(fail_mode="http_error")
    cmd = ingest_cmd(instrument, _BASE, _BASE + timedelta(minutes=1), venue=Venue.BITGET)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        await run_ingest(deps, cmd, source, clock_at=_BASE)
    assert len(source.fetch_calls) == 1


def _measure(perf_budget: PerfBudget, *, delay_s: float = 0.0):
    cols = _columns(300)
    cal = _calendar()

    def _run() -> object:
        return rollup(cols, Timeframe.M5, cal)

    call = virtual_delayed_sync(_run, delay_s) if delay_s else _run
    return perf_budget.best_of(call, n=5, warmup=1)


@pytest.mark.perf
def test_rollup_perf_budget(perf_budget: PerfBudget) -> None:
    """성능 단언: 300 M1 -> M5 rollup best-of-5 가 예산 미만."""
    reset_virtual_totals()
    sample = _measure(perf_budget)
    assert sample.cpu_ms < _ROLLUP_BUDGET_MS, perf_budget.describe(
        sample, budget_ms=_ROLLUP_BUDGET_MS
    )


@pytest.mark.perf
def test_rollup_perf_gate_red_with_10x_injection(perf_budget: PerfBudget) -> None:
    """게이트 적색 재현: 예산 10배 가상 지연을 주입하면 같은 예산 단언이 적색이 된다."""
    reset_virtual_totals()
    sample = _measure(perf_budget, delay_s=_ROLLUP_BUDGET_MS * 10 / 1000)
    assert not (sample.cpu_ms < _ROLLUP_BUDGET_MS)
    reset_virtual_totals()
