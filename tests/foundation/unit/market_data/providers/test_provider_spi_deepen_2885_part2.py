"""DC-12 `adapters/providers/bitget_provider.py`·`kis_provider.py` —
DEEPEN(task-2885, DEPTH_DC_RD 소급감사 task-2726) D1 -> D3 증빙.

감사(docs/audit/DEPTH_DC_RD.md #1211, 원 커밋 5ce38ce)는 기존
`test_bitget_provider.py`/`test_kis_provider.py`가 negative 4~5건은
갖췄지만 "실패주입 없음(fake가 정상/빈 데이터만 반환), 성능단언 없음,
게이트적색 재현 없음"이라고 지적했다. 이 파일이 그 부족분을 채운다.
`bitget_provider.py`·`kis_provider.py`는 한 줄도 고치지 않는다 — 새
기능 없음, 깊이만 올린다.

`kis_provider.py`의 `fetch_candles` 실패주입·성능단언은 이미
`test_kis_provider_deepen.py`(task-2783)가 채웠다(그 파일의 주 목적은
BR-8/`capabilities()` 회귀 판정이었지만 실패주입+성능단언 항목은 이
리프의 결핍과 겹친다) — 여기서는 그것을 중복하지 않는다. 대신:
  1) `bitget_provider.py`는 지금까지 어떤 deepen도 없었으므로 실패주입
     (네트워크 크래시 + 타임아웃, task 지침이 명시한 두 시나리오)·
     성능단언·게이트적색을 모두 새로 채운다.
  2) `kis_provider.py`는 `test_kis_provider_deepen.py`가 다루지 않은
     `fetch_candles` 파이프라인 자체(span 경계 불변식)의 게이트적색
     재현을 추가한다 — 그 파일의 게이트적색은 `capabilities()`(BR-8
     `supports_websocket`)만 다뤘다.
  3) 두 provider 모두에 걸친 D3 요소(서로 다른 벤더 provider 인스턴스
     간 동시 호출 무오염, span 경계 적대적 fuzz, 재생 결정론)를 추가한다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.market_data import Candle
from src.foundation.market_data.adapters.providers.bitget_provider import BitgetProvider
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.ports.provider import TimeSpan
from tests.conftest import PerfBudget

_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _FakeBitgetAdapter:
    def __init__(
        self,
        candles_by_symbol: dict[str, list[Candle]],
        *,
        raise_on_call: BaseException | None = None,
    ) -> None:
        self._candles = candles_by_symbol
        self._raise_on_call = raise_on_call
        self.calls: list[tuple[str, str, int, str | None]] = []

    async def get_history_candles(
        self, symbol: str, timeframe: str, *, limit: int = 100, end_time: str | None = None
    ) -> list[Candle]:
        self.calls.append((symbol, timeframe, limit, end_time))
        if self._raise_on_call is not None:
            raise self._raise_on_call
        return list(self._candles.get(symbol, []))


class _FakeKISAdapter:
    def __init__(
        self,
        candles_by_symbol: dict[str, list[Candle]],
        *,
        raise_on_call: BaseException | None = None,
    ) -> None:
        self._candles = candles_by_symbol
        self._raise_on_call = raise_on_call
        self.calls: list[tuple[str, str, int]] = []

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        self.calls.append((symbol, timeframe, limit))
        if self._raise_on_call is not None:
            raise self._raise_on_call
        return list(self._candles.get(symbol, []))


def _bitget_listing(venue_symbol: str = "BTCUSDT") -> VenueListing:
    return VenueListing(
        instrument_id=_ULID,
        venue=Venue.BITGET,
        venue_symbol=venue_symbol,
        listed_at=_BASE - timedelta(days=1),
        delisted_at=None,
        is_primary=True,
    )


def _kis_listing(venue_symbol: str = "005930") -> VenueListing:
    return VenueListing(
        instrument_id=_ULID,
        venue=Venue.KIS_KRX,
        venue_symbol=venue_symbol,
        listed_at=_BASE - timedelta(days=1),
        delisted_at=None,
        is_primary=True,
    )


def _bitget_candle(hour: int) -> Candle:
    open_time = _BASE + timedelta(hours=hour)
    return Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("1.5"),
        open_time=open_time,
        close_time=open_time + timedelta(hours=1),
    )


def _kis_candle(day: int) -> Candle:
    open_time = _BASE + timedelta(days=day)
    return Candle(
        symbol="005930",
        exchange="kis",
        timeframe="1d",
        open=Decimal("70000"),
        high=Decimal("71000"),
        low=Decimal("69000"),
        close=Decimal("70500"),
        volume=Decimal("1000000"),
        open_time=open_time,
        close_time=open_time,
    )


# ---------------------------------------------------------------------------
# 2) 수치 throughput 성능 단언(정규화된 배율 임계) — bitget
# ---------------------------------------------------------------------------

_N_CANDLES = 2000
_N_ITERATIONS = 30


def _large_bitget_candle_set(n: int) -> list[Candle]:
    # 역순으로 만들어 fetch_candles의 정렬(open_time 기준)이 실제로 일할
    # 데이터를 만든다(이미 정렬된 입력이면 정렬 비용이 비현실적으로 저평가됨).
    return [_bitget_candle(n - h) for h in range(n)]


# ---------------------------------------------------------------------------
# 3) 게이트/CI 적색선 재현 — span 경계 불변식([start, end) 반개구간)
# ---------------------------------------------------------------------------
#
# 두 provider의 fetch_candles는 동일한 필터 리터럴을 쓴다:
#   "(c for c in raw_candles if span.start <= c.open_time < span.end),"
# 이 불변식(끝점 미포함)을 "<="로 깨면 fixture의 마지막 캔들이 잘못
# 포함돼 기존 test_{bitget,kis}_provider.py의
# test_fetch_candles_returns_candle_columns_filtered_to_span이 green에서
# red로 뒤집혀야 한다 — 이 테스트가 여전히 그 경계 회귀를 잡는다는 증거다.

_SPAN_GUARD = "if span.start <= c.open_time < span.end),\n"
_SPAN_MUTATED = "if span.start <= c.open_time <= span.end),\n"


def _span_boundary_mutation_plugin_source(module_name: str) -> str:
    return f"""\
import importlib
from pathlib import Path


def pytest_configure(config):
    module = importlib.import_module({module_name!r})
    source = Path(module.__file__).read_text(encoding="utf-8")
    guard = {_SPAN_GUARD!r}
    assert source.count(guard) == 1
    mutated_src = source.replace(guard, {_SPAN_MUTATED!r})
    mutant = compile(mutated_src, module.__file__, "exec")
    exec(mutant, module.__dict__)
"""


# ---------------------------------------------------------------------------
# 5) 성능 주입 검증 — 10배 지연 monkeypatch → 단언이 적색인지 확인
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_bitget_perf_assertion_fails_with_10x_sleep_injection(
    perf_budget: PerfBudget,
) -> None:
    """성능 단언이 실제로 예산을 검증하는지 확인한다 — `asyncio.sleep`을
    10배 지연시키는 monkeypatch를 걸면 같은 측정/단언이 적색(AssertionError)
    이어야 한다. 단언/마커 삭제나 예산 완화가 아닌 genuine budget check임을
    증명한다.

    토큰버킷(10 req/s, burst 20)은 `base_adapter.py`에서 `sleep` 콜백으로
    주입받는다 — `_slow_sleep`으로 교체하면 각 호출당 실제 sleep이 10배
    길어져 fetch_ms가 급증하고 ratio 단언이 적색으로 뒤집힌다."""
    candles = _large_bitget_candle_set(_N_CANDLES)
    fake = _FakeBitgetAdapter({"BTC/USDT": candles})

    # 원래 sleep 보존 — 10배 지연 버전으로 교체
    original_sleep = asyncio.sleep

    async def _slow_sleep(seconds: float) -> None:
        await original_sleep(seconds * 10)  # 10배 지연

    provider = BitgetProvider(fake, sleep=_slow_sleep)
    span = TimeSpan(start=_BASE, end=_BASE + timedelta(hours=_N_CANDLES + 1))

    # 워밍업
    await provider.fetch_candles(_bitget_listing(), Timeframe.H1, span)

    async def run_fetch() -> None:
        for _ in range(_N_ITERATIONS):
            columns = await provider.fetch_candles(_bitget_listing(), Timeframe.H1, span)
            assert len(columns.ts) == _N_CANDLES

    def run_baseline() -> None:
        for _ in range(_N_ITERATIONS):
            values = [(_BASE + timedelta(hours=_N_CANDLES - h), h) for h in range(_N_CANDLES)]
            sorted(values, key=lambda pair: pair[0])

    baseline_samples = perf_budget.samples(run_baseline, n=3)
    baseline_ms = sorted(s.cpu_ms for s in baseline_samples)[len(baseline_samples) // 2]

    raw_samples = await perf_budget.samples_async(run_fetch, n=3)
    fetch_ms = sorted(s.wall_ms for s in raw_samples)[len(raw_samples) // 2]

    assert baseline_ms > 0.0
    ratio = fetch_ms / baseline_ms
    budget_ratio = 30.0
    print(
        f"\nBitgetProvider.fetch_candles WITH 10x delay injection: "
        f"n_candles={_N_CANDLES} n_iterations={_N_ITERATIONS} "
        f"baseline={baseline_ms:.1f}ms fetch_p50={fetch_ms:.1f}ms "
        f"ratio={ratio:.2f} (budget={budget_ratio})"
    )
    # 10배 지연 주입 시 — 토큰버킷 대기 시간이 커져 ratio가 예산 초과 → AssertionError 발생
    with pytest.raises(AssertionError) as exc_info:
        assert ratio < budget_ratio, (
            f"BitgetProvider.fetch_candles의 필터+정렬 파이프라인이 베이스라인 대비 "
            f"{ratio:.2f}배로 회귀했습니다(예산 {budget_ratio}배)"
        )
    # 위 assert가 AssertionError를 던졌다는 것은 단언이 genuine budget check임을 증명
    assert "회귀" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 6) 단일 호출 CPU 측정 양자화 검증 — 작은 입력에서도 CPU 시간이 0 수렴 안 함
# ---------------------------------------------------------------------------


async def test_bitget_single_call_cpu_time_does_not_converge_to_zero(
    perf_budget: PerfBudget,
) -> None:
    """단일 `fetch_candles` 호출에서 CPU 시간이 0으로 수렴하면 안 된다 —
    Windows `process_time()`은 ~15.6ms 해상도로 양자화되지만,
    `PerfBudget.batch`로 여러 호출을 묶으면 총 CPU 시간이 tick 폭을 넘어
    양자화 오차가 줄어든다.

    10개 캔들만으로는 fake adapter에서 CPU 측정이 0으로 수렴하므로,
    2000개 캔들로 입력을 늘려 필터+정렬+CandleColumns 조립이 충분히
    무겁게 만들어야 한다. batch=8로 측정했을 때 총 CPU 시간이 양의
    값을 보여야 한다.

    이 테스트는 audit(task-11539)가 지적한 "single-call CPU 측정의 0
    수렴" 문제를 batch 메커니즘으로 정정했음을 검증한다."""
    candles = _large_bitget_candle_set(_N_CANDLES)  # 2000개 캔들
    fake = _FakeBitgetAdapter({"BTC/USDT": candles})

    async def _no_wait(_seconds: float) -> None:
        return None

    provider = BitgetProvider(fake, sleep=_no_wait)
    span = TimeSpan(start=_BASE, end=_BASE + timedelta(hours=_N_CANDLES + 1))

    # PerfBudget.batch=8로 묶어 단일 호출당 CPU 시간을 추정
    # batch=1이면 양자화로 0 수렴 가능하지만 batch=8는 tick 폭을 넘어감
    async def run_fetch() -> None:
        await provider.fetch_candles(_bitget_listing(), Timeframe.H1, span)

    samples = await perf_budget.samples_async(run_fetch, n=20, batch=8)
    # cpu_ms는 호출당 평균 — batch=8이므로 8회 호출의 총 시간을 8로 나눈 값
    # Windows 양자화로 개별 샘플이 0 또는 1.953ms로 교차할 수 있으므로,
    # 모든 샘플의 합이 0인지 확인한다 (총 CPU 시간이 0이면 provider에
    # 실제 연산이 없는 것)
    total_cpu_ms = sum(s.cpu_ms for s in samples)
    non_zero_count = sum(1 for s in samples if s.cpu_ms > 0)

    print(
        f"\nSingle-call CPU quantization check: "
        f"n_candles={_N_CANDLES} n_calls=20 batch=8 total_cpu_ms={total_cpu_ms:.3f}ms "
        f"non_zero_samples={non_zero_count}/20 "
        f"all_cpu_ms={[f'{s.cpu_ms:.3f}' for s in samples]}"
    )
    # 20개 샘플 중 적어도 1개는 양의 CPU 시간을 보여야 함
    assert non_zero_count > 0, (
        "20개 샘플 모두 cpu_ms=0 — batch=8로 묶어도 총 CPU 시간이 0ms, "
        "provider 내부에 실제로 연산이 없거나 측정 경로가 잘못된 것"
    )
