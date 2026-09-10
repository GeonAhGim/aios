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
import importlib
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.core.exceptions import RetryableExchangeError
from src.data.models.market_data import Candle
from src.foundation.market_data.adapters.providers.bitget_provider import BitgetProvider
from src.foundation.market_data.adapters.providers.kis_provider import KISProvider
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.ports.provider import DataProviderError, TimeSpan

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
# 1) 실패주입 — bitget: 네트워크 크래시 + 타임아웃 (둘 다 기존 deepen 없음)
# ---------------------------------------------------------------------------


async def test_bitget_fetch_candles_network_crash_is_not_swallowed_into_coverage_missing() -> None:
    """`get_history_candles`가 네트워크 단절로 `RetryableExchangeError`를
    던지면 `call_with_retry`(base_adapter.py, `except DataProviderError`만
    잡음)가 이를 그대로 전파해야 한다 — `DATA_COVERAGE_MISSING`(정상적인
    "그 구간에 거래 없음")으로 둔갑하거나 빈 `CandleColumns`로 흡수되면,
    실제 장애가 "그냥 그 구간에 거래 없음"으로 오인된다."""
    fake = _FakeBitgetAdapter({}, raise_on_call=RetryableExchangeError("simulated network outage"))
    provider = BitgetProvider(fake)
    span = TimeSpan(start=_BASE, end=_BASE + timedelta(hours=1))

    with pytest.raises(RetryableExchangeError):
        await provider.fetch_candles(_bitget_listing(), Timeframe.H1, span)

    assert len(fake.calls) == 1  # 실제로 위임을 시도했다(조용히 건너뛰지 않음)


async def test_bitget_fetch_candles_timeout_is_not_swallowed_into_coverage_missing() -> None:
    """네트워크 크래시와 별개 시나리오 — 원격 호출이 타임아웃(`TimeoutError`)
    나면 그 역시 `DataProviderError`가 아니므로 `call_with_retry`가 즉시
    전파해야 한다(재시도 없이, 삼켜지지도 않고)."""
    fake = _FakeBitgetAdapter({}, raise_on_call=TimeoutError("simulated request timeout"))
    provider = BitgetProvider(fake)
    span = TimeSpan(start=_BASE, end=_BASE + timedelta(hours=1))

    with pytest.raises(TimeoutError):
        await provider.fetch_candles(_bitget_listing(), Timeframe.H1, span)

    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# 2) 수치 throughput 성능 단언(정규화된 배율 임계) — bitget
# ---------------------------------------------------------------------------

_N_CANDLES = 2000
_N_ITERATIONS = 30


def _large_bitget_candle_set(n: int) -> list[Candle]:
    # 역순으로 만들어 fetch_candles의 정렬(open_time 기준)이 실제로 일할
    # 데이터를 만든다(이미 정렬된 입력이면 정렬 비용이 비현실적으로 저평가됨).
    return [_bitget_candle(n - h) for h in range(n)]


async def test_bitget_fetch_candles_filter_sort_throughput_bounded_vs_trivial_baseline() -> None:
    """`fetch_candles`의 구간 필터+정렬 파이프라인이 대용량(2000개) 캔들에서
    30회 반복 처리하는 총소요시간이, 같은 프로세스가 방금 측정한 구조적으로
    동등한 트리비얼 정렬 베이스라인 대비 좁은 배율 범위여야 한다. 절대 ms
    상수 대신 정규화 배율을 쓴다(test_kis_provider_deepen.py 선례와 동일
    판단 — 공유 CI 환경에서 절대 임계는 상시 적색을 낳는다)."""
    candles = _large_bitget_candle_set(_N_CANDLES)
    fake = _FakeBitgetAdapter({"BTC/USDT": candles})
    provider = BitgetProvider(fake)
    span = TimeSpan(start=_BASE, end=_BASE + timedelta(hours=_N_CANDLES + 1))

    # 워밍업 — import/최초 호출 1회성 비용이 표본에 섞이지 않게 한다.
    await provider.fetch_candles(_bitget_listing(), Timeframe.H1, span)

    async def run_fetch() -> None:
        for _ in range(_N_ITERATIONS):
            columns = await provider.fetch_candles(_bitget_listing(), Timeframe.H1, span)
            assert len(columns.ts) == _N_CANDLES

    def run_baseline() -> None:
        for _ in range(_N_ITERATIONS):
            values = [(_BASE + timedelta(hours=_N_CANDLES - h), h) for h in range(_N_CANDLES)]
            sorted(values, key=lambda pair: pair[0])

    baseline_start = time.perf_counter()
    run_baseline()
    baseline_seconds = time.perf_counter() - baseline_start

    fetch_start = time.perf_counter()
    await run_fetch()
    fetch_seconds = time.perf_counter() - fetch_start

    assert baseline_seconds > 0.0
    ratio = fetch_seconds / baseline_seconds
    budget_ratio = 30.0  # fetch는 필터+정렬+CandleColumns 조립+비동기 호출
    # 오버헤드가 더해져 트리비얼 정렬 베이스라인보다 근본적으로 느리다.
    print(
        f"\nBitgetProvider.fetch_candles filter+sort throughput vs trivial baseline: "
        f"n_candles={_N_CANDLES} n_iterations={_N_ITERATIONS} "
        f"baseline={baseline_seconds * 1000:.1f}ms fetch={fetch_seconds * 1000:.1f}ms "
        f"ratio={ratio:.2f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"BitgetProvider.fetch_candles의 필터+정렬 파이프라인이 베이스라인 대비 "
        f"{ratio:.2f}배로 회귀했습니다(예산 {budget_ratio}배) — span 필터/정렬/"
        "CandleColumns 조립 경로에 의도치 않은 무거운 연산이 섞였을 가능성."
    )


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


@pytest.mark.parametrize(
    "provider_module,target_test",
    [
        (
            "src.foundation.market_data.adapters.providers.bitget_provider",
            "tests/foundation/unit/market_data/providers/test_bitget_provider.py::"
            "test_fetch_candles_returns_candle_columns_filtered_to_span",
        ),
        (
            "src.foundation.market_data.adapters.providers.kis_provider",
            "tests/foundation/unit/market_data/providers/test_kis_provider.py::"
            "test_fetch_candles_returns_candle_columns_filtered_to_span",
        ),
    ],
)
def test_pytest_gate_turns_red_when_span_end_boundary_is_widened(
    provider_module: str, target_test: str, tmp_path: Path
) -> None:
    """task 지침 — "구현 쪽을 되돌리면 테스트가 FAIL로 돌아오는가" 실측.
    `[span.start, span.end)`의 끝점 배타를 자식 pytest 프로세스 안에서만
    끝점 포함(`<=`)으로 넓히면(프로덕션 소스는 그대로), 기존 필터-스팬
    happy-path 테스트가 green(1 passed)에서 red(1 failed)로 뒤집힘을
    증명한다(test_kis_provider_deepen.py의 BR-8 게이트적색과 동일 기법을
    fetch_candles 파이프라인 자체에 적용)."""
    module = importlib.import_module(provider_module)
    assert Path(module.__file__).read_text(encoding="utf-8").count(_SPAN_GUARD) == 1

    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", target_test]
    repo_root = str(Path.cwd())
    env = dict(os.environ, PYTHONPATH=repo_root, PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8")

    baseline = subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
        check=False,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    plugin_module_name = f"_mutate_span_boundary_{provider_module.rsplit('.', 1)[-1]}"
    plugin = tmp_path / f"{plugin_module_name}.py"
    plugin.write_text(_span_boundary_mutation_plugin_source(provider_module), encoding="utf-8")
    mutated_env = dict(env, PYTHONPATH=f"{repo_root}{os.pathsep}{tmp_path}")

    mutated = subprocess.run(
        [*command[:-1], "-p", plugin_module_name, command[-1]],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=mutated_env,
        timeout=120,
        check=False,
    )
    assert mutated.returncode != 0, mutated.stdout + mutated.stderr
    assert "1 passed" not in mutated.stdout
    assert "1 failed" in mutated.stdout


# ---------------------------------------------------------------------------
# 4) D3 — 벤더 provider 다중 인스턴스 동시 호출 무오염 / 적대적 span fuzz /
#    재생 결정론
# ---------------------------------------------------------------------------


async def test_concurrent_bitget_and_kis_providers_do_not_cross_contaminate() -> None:
    """서로 다른 벤더 provider(각자 독립된 페이크 어댑터·버킷을 가짐) 3개
    — bitget 성공, bitget 네트워크 크래시, kis 성공 — 를 동시에 굴려도
    한 provider의 실패가 다른 provider의 호출 횟수나 결과에 영향을
    주면 안 된다."""
    ok_bitget = BitgetProvider(
        _FakeBitgetAdapter({"BTC/USDT": [_bitget_candle(h) for h in range(3)]})
    )
    crashing_bitget = BitgetProvider(
        _FakeBitgetAdapter({}, raise_on_call=RetryableExchangeError("outage"))
    )
    ok_kis = KISProvider(_FakeKISAdapter({"005930": [_kis_candle(d) for d in range(3)]}))

    bitget_span = TimeSpan(start=_BASE, end=_BASE + timedelta(hours=3))
    kis_span = TimeSpan(start=_BASE, end=_BASE + timedelta(days=3))

    results = await asyncio.gather(
        ok_bitget.fetch_candles(_bitget_listing(), Timeframe.H1, bitget_span),
        crashing_bitget.fetch_candles(_bitget_listing(), Timeframe.H1, bitget_span),
        ok_kis.fetch_candles(_kis_listing(), Timeframe.D1, kis_span),
        return_exceptions=True,
    )

    assert results[0].ts == [_BASE + timedelta(hours=h) for h in (0, 1, 2)]
    assert isinstance(results[1], RetryableExchangeError)
    assert results[2].ts == [_BASE + timedelta(days=d) for d in (0, 1, 2)]


@pytest.mark.parametrize(
    "start_offset_hours,end_offset_hours,expected_len",
    [
        (0, 0, 0),  # 영구간(start==end) — 아무것도 포함하지 않아야 한다
        (5, 4, 0),  # 역전 구간(start>end) — 항상 공집합
        (-100, -50, 0),  # fixture보다 한참 과거 — 전부 벗어남
        (100, 200, 0),  # fixture보다 한참 미래 — 전부 벗어남
        (0, 5, 5),  # fixture 전체를 정확히 덮음(반개구간 경계 포함/배제 확인)
    ],
)
async def test_bitget_fetch_candles_adversarial_span_boundaries(
    start_offset_hours: int, end_offset_hours: int, expected_len: int
) -> None:
    """적대적 span 경계값(영구간·역전·완전히 벗어난 구간·정확히 덮는 구간)
    각각에서 필터 결과 개수가 기대와 정확히 일치해야 한다 — 하나라도
    어긋나면 조용한 과다/과소 포함(§4.1 위반)이다. 결과가 비면
    `DATA_COVERAGE_MISSING`으로 fail-closed 해야 한다."""
    fake = _FakeBitgetAdapter({"BTC/USDT": [_bitget_candle(h) for h in range(5)]})
    provider = BitgetProvider(fake)
    span = TimeSpan(
        start=_BASE + timedelta(hours=start_offset_hours),
        end=_BASE + timedelta(hours=end_offset_hours),
    )

    if expected_len == 0:
        with pytest.raises(DataProviderError):
            await provider.fetch_candles(_bitget_listing(), Timeframe.H1, span)
    else:
        columns = await provider.fetch_candles(_bitget_listing(), Timeframe.H1, span)
        assert len(columns.ts) == expected_len


async def test_bitget_fetch_candles_replay_with_same_input_is_deterministic() -> None:
    """동일한 페이크 어댑터·listing·span으로 `fetch_candles`를 두 번
    호출하면 결과(`CandleColumns`의 각 컬럼)가 정확히 같아야 한다 —
    숨겨진 전역 상태(시각·난수·정렬 불안정성)에 의존하지 않는다는 증거."""
    fake = _FakeBitgetAdapter({"BTC/USDT": [_bitget_candle(h) for h in range(5)]})
    provider = BitgetProvider(fake)
    span = TimeSpan(start=_BASE, end=_BASE + timedelta(hours=5))

    first = await provider.fetch_candles(_bitget_listing(), Timeframe.H1, span)
    second = await provider.fetch_candles(_bitget_listing(), Timeframe.H1, span)

    assert first.ts == second.ts
    assert first.open == second.open
    assert first.close == second.close
    assert len(fake.calls) == 2  # 두 번 다 실제로 위임했다(캐시로 둔갑하지 않음)
