"""task-2783 DEEPEN of task-1924 (BR-8/DC-12 KIS provider layer, ADR-2026-09-06-I D2).

DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md #1924)는 원 task-1924
(commit fb1b93e — KIS 해외주식/capabilities pytest 5건 적색 수정)가 D2
하한에 미달(실측 D1)이라고 판정했다: negative test는 3개 이상 있었지만
automated failure-injection test·수치 performance 단언이 없었고, 커밋
메시지의 "적색→녹색" 증명이 수동 1회성 확인이지 커밋된 반복 가능 테스트가
아니었다.

원인 판정(task 지침 요구사항) — task-1924가 고친 5건의 실패 중
`test_capabilities_matches_real_kis_adapter_declaration`(이 파일이 다루는
`tests/foundation/unit/market_data/providers/test_kis_provider.py`)은
task-1786(commit 7d2b7ef, BR-8)가 `KISAdapter.get_capabilities()`를
KR_EQUITY 단일 선언에서 10개 자산군 + `supports_websocket=True`로 넓힌
뒤, 옛 KR_EQUITY 단일 선언을 기대하던 이 테스트가 새 계약을 반영하도록
갱신된 것이다 — "구현이 틀렸다"가 아니라 "테스트가 옛 계약을 붙들고
있었다"는 판정이다(다른 4건, 해외주식 거래소 전수 매핑은
tests/unit/exchanges/kis/test_overseas_stock_mixin_deepen.py — task-2776
— 가 이미 같은 판정과 회귀 증명을 이 리프와 겹치지 않는 범위에서 마쳤다).

이 파일은 그 판정의 나머지 절반(BR-8/kis_provider.py 계층)만 다룬다.
겹치지 않는 각도로 잡았다:
  - tests/unit/exchanges/kis/test_overseas_stock_mixin_deepen.py(task-2776)
    는 `overseas_stock_mixin.py`(place/cancel/balance)만 다룬다.
  - tests/integration/exchanges/test_kis_overseas_deepen.py(task-2772)는
    `KISAdapter`의 시세조회/주문 디스패치 경로(failure-injection+perf+
    gate-red)를 다룬다.
  - tests/integration/exchanges/test_kis_capability_matrix_deepen.py
    (task-2780)는 `order_dispatch.dispatch_place_order`(자산군별 라우팅)
    를 다룬다.
  이 중 누구도 `src/foundation/market_data/adapters/providers/
  kis_provider.py`(DC-12 SPI 위임 계층, `KISProvider`) 자체를 건드리지
  않는다 — 이 파일이 그 공백을 채운다.

1) failure-injection: `KISProvider.fetch_candles`는 `BaseProviderAdapter.
   call_with_retry`에 위임하는데, 그 재시도 루프는 `except DataProviderError`
   만 잡는다(base_adapter.py) — 실제 `KISAdapter.get_ohlcv`가 네트워크
   단절로 `RetryableExchangeError`를 던지는 상황을 페이크로 시뮬레이션해,
   그 예외가 삼켜지거나 "빈 구간"(`DATA_COVERAGE_MISSING`, 정상적인 무거래
   상황과 동일한 코드)으로 둔갑하지 않고 원본 그대로 드러남을 증명한다 —
   후자로 둔갑하면 "실제 장애"와 "그냥 그 구간에 거래가 없었다"를 호출부가
   구분할 수 없게 된다.
2) 수치 throughput 성능 단언: `fetch_candles`의 구간 필터+정렬 파이프라인
   (`span.start <= c.open_time < span.end` 필터 + `open_time` 기준 정렬)을
   대용량 캔들에서 반복 측정해, 같은 프로세스가 방금 측정한 구조적으로
   동등한 트리비얼 정렬 베이스라인 대비 정규화 배율로 검증한다(절대 ms
   상수 대신 — task-2776/2778/2779 선례와 동일 판단).
3) 게이트/CI 적색선 회귀: task 지침이 요구하는 "구현 쪽 매핑을 되돌리면
   테스트가 FAIL로 돌아오는가" 실측을 BR-8/kis_provider 축에서 수행한다.
   `KISAdapter.get_capabilities()`의 `supports_websocket=True`를 옛
   선언(BR-8 이전 가정)인 `False`로 자식 pytest 프로세스 안에서만
   되돌리면(프로덕션 소스는 그대로), 원 5건 실패 중 하나였던
   `test_capabilities_matches_real_kis_adapter_declaration`이 green(1
   passed)에서 red(1 failed)로 뒤집힘을 증명한다(동일 기법,
   test_overseas_stock_mixin_deepen.py 선례).
"""
from __future__ import annotations

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
from src.foundation.market_data.adapters.providers.kis_provider import KISProvider
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.ports.provider import TimeSpan

_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _FakeKISAdapter:
    """`get_ohlcv`만 흉내 내는 페이크(test_kis_provider.py와 동일 원칙).
    `raise_on_call`을 지정하면 그 예외를 그대로 던져 network-failure
    페이크로도 쓸 수 있다."""

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


def _listing(venue_symbol: str = "005930") -> VenueListing:
    return VenueListing(
        instrument_id=_ULID,
        venue=Venue.KIS_KRX,
        venue_symbol=venue_symbol,
        listed_at=_BASE - timedelta(days=1),
        delisted_at=None,
        is_primary=True,
    )


def _candle(day: int) -> Candle:
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
# 1) failure-injection
# ---------------------------------------------------------------------------


async def test_fetch_candles_network_failure_is_not_swallowed_into_coverage_missing() -> None:
    """`get_ohlcv`가 네트워크 단절로 `RetryableExchangeError`를 던지면
    `call_with_retry`(base_adapter.py, `except DataProviderError`만 잡음)가
    이를 그대로 전파해야 한다 — `DATA_COVERAGE_MISSING`(정상적인 "그 구간에
    거래 없음")으로 둔갑하거나 빈 `CandleColumns`로 흡수되면, 실제 장애가
    "그냥 휴장/거래없음"으로 오인돼 백테스트/스캐너가 조용히 데이터 결측을
    삼키게 된다."""
    fake = _FakeKISAdapter({}, raise_on_call=RetryableExchangeError("simulated network outage"))
    provider = KISProvider(fake)
    span = TimeSpan(start=_BASE, end=_BASE + timedelta(days=1))

    with pytest.raises(RetryableExchangeError):
        await provider.fetch_candles(_listing(), Timeframe.D1, span)

    assert len(fake.calls) == 1  # 실제로 위임을 시도했다(조용히 건너뛰지 않음)


# ---------------------------------------------------------------------------
# 2) 수치 throughput 성능 단언(정규화된 배율 임계)
# ---------------------------------------------------------------------------

_N_CANDLES = 2000
_N_ITERATIONS = 30


def _large_candle_set(n: int) -> list[Candle]:
    # 역순으로 만들어 fetch_candles의 정렬(open_time 기준)이 실제로 일할
    # 데이터를 만든다(이미 정렬된 입력이면 정렬 비용이 비현실적으로 저평가됨).
    return [_candle(n - d) for d in range(n)]


async def test_fetch_candles_filter_sort_throughput_bounded_vs_trivial_baseline() -> None:
    """`fetch_candles`의 구간 필터+정렬 파이프라인이 대용량(2000개) 캔들에서
    30회 반복 처리하는 총소요시간이, 같은 프로세스가 방금 측정한 구조적으로
    동등한 트리비얼 정렬 베이스라인(같은 N의 리스트를 comparison-key 정렬)
    대비 좁은 배율 범위여야 한다. 절대 ms 상수 대신 정규화 배율을 쓴다
    (task-2776/2778/2779 선례와 동일 판단 — 공유 CI 환경에서 절대 임계는
    상시 적색을 낳는다)."""
    candles = _large_candle_set(_N_CANDLES)
    fake = _FakeKISAdapter({"005930": candles})
    provider = KISProvider(fake)
    span = TimeSpan(start=_BASE, end=_BASE + timedelta(days=_N_CANDLES + 1))

    # 워밍업 — import/최초 호출 1회성 비용이 표본에 섞이지 않게 한다.
    await provider.fetch_candles(_listing(), Timeframe.D1, span)

    async def run_fetch() -> None:
        for _ in range(_N_ITERATIONS):
            columns = await provider.fetch_candles(_listing(), Timeframe.D1, span)
            assert len(columns.ts) == _N_CANDLES

    def run_baseline() -> None:
        for _ in range(_N_ITERATIONS):
            values = [(_BASE + timedelta(days=_N_CANDLES - d), d) for d in range(_N_CANDLES)]
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
        f"\nKISProvider.fetch_candles filter+sort throughput vs trivial baseline: "
        f"n_candles={_N_CANDLES} n_iterations={_N_ITERATIONS} "
        f"baseline={baseline_seconds * 1000:.1f}ms fetch={fetch_seconds * 1000:.1f}ms "
        f"ratio={ratio:.2f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"KISProvider.fetch_candles의 필터+정렬 파이프라인이 베이스라인 대비 "
        f"{ratio:.2f}배로 회귀했습니다(예산 {budget_ratio}배) — span 필터/정렬/"
        "CandleColumns 조립 경로에 의도치 않은 무거운 연산이 섞였을 가능성."
    )


# ---------------------------------------------------------------------------
# 3) 게이트/CI 적색선 회귀 — BR-8 supports_websocket 선언을 옛 값으로 되돌림
# ---------------------------------------------------------------------------

_GUARD = "            supports_websocket=True,\n"
_MUTATED = "            supports_websocket=False,\n"


def _plugin_source() -> str:
    return f"""\
import importlib
from pathlib import Path


def pytest_configure(config):
    module = importlib.import_module("src.exchanges.kis.adapter")
    source = Path(module.__file__).read_text(encoding="utf-8")
    guard = {_GUARD!r}
    assert source.count(guard) == 1
    mutated_src = source.replace(guard, {_MUTATED!r})
    mutant = compile(mutated_src, module.__file__, "exec")
    exec(mutant, module.__dict__)
"""


def test_pytest_gate_turns_red_when_br8_websocket_declaration_is_reverted(
    tmp_path: Path,
) -> None:
    """task 지침 — "테스트를 고치는 쪽으로 판정했다면 그 테스트가 여전히
    회귀를 잡는다는 것을 증명해야 한다": `KISAdapter.get_capabilities()`의
    `supports_websocket=True`(BR-8, task-1786)를 자식 pytest 프로세스
    안에서만 옛 값(`False`)으로 되돌리면(프로덕션 소스는 그대로),
    task-1924가 고친 원 5건 실패 중 하나였던
    `test_capabilities_matches_real_kis_adapter_declaration`이 green(1
    passed)에서 red(1 failed)로 뒤집힘을 실측한다 — 이 테스트가 여전히
    BR-8 회귀를 잡는다는 반복 가능한 증거다."""
    target_test = (
        "tests/foundation/unit/market_data/providers/test_kis_provider.py::"
        "test_capabilities_matches_real_kis_adapter_declaration"
    )
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", target_test]
    repo_root = str(Path.cwd())
    env = dict(os.environ, PYTHONPATH=repo_root, PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8")

    baseline = subprocess.run(
        command, capture_output=True, encoding="utf-8", errors="replace",
        env=env, timeout=120, check=False,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    plugin_module_name = "_mutate_br8_websocket_declaration"
    plugin = tmp_path / f"{plugin_module_name}.py"
    plugin.write_text(_plugin_source(), encoding="utf-8")
    mutated_env = dict(env, PYTHONPATH=f"{repo_root}{os.pathsep}{tmp_path}")

    mutated = subprocess.run(
        [*command[:-1], "-p", plugin_module_name, command[-1]],
        capture_output=True, encoding="utf-8", errors="replace",
        env=mutated_env, timeout=120, check=False,
    )
    assert mutated.returncode != 0, mutated.stdout + mutated.stderr
    assert "1 passed" not in mutated.stdout
    assert "1 failed" in mutated.stdout
