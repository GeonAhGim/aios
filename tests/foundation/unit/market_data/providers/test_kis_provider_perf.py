"""task-21616: KISProvider fetch_candles throughput test split from
test_kis_provider_deepen.py.

Performance-only tests for KISProvider.fetch_candles — extracted so the
failure-injection / gate-red tests in test_kis_provider_deepen.py stay
lightweight and fast.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.foundation.market_data.adapters.providers.kis_provider import KISProvider
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.ports.provider import TimeSpan

from .test_kis_provider_deepen import (
    _BASE,
    _N_CANDLES,
    _N_ITERATIONS,
    _FakeKISAdapter,
    _large_candle_set,
    _listing,
)


@pytest.mark.perf
async def test_fetch_candles_filter_sort_throughput_bounded_vs_trivial_baseline(
    perf_budget,
) -> None:
    """`fetch_candles`의 구간 필터+정렬 파이프라인이 대용량(2000개) 캔들에서
    30회 반복 처리하는 총소요시간이, 같은 프로세스가 방금 측정한 구조적으로
    동등한 트리비얼 정렬 베이스라인(같은 N의 리스트를 comparison-key 정렬)
    대비 좁은 배율 범위여야 한다. 절대 ms 상수 대신 정규화 배율을 쓴다
    (task-2776/2778/2779 선례와 동일 판단 — 공유 CI 환경에서 절대 임계는
    상시 적색을 낳는다).

    `KISProvider`(`base_adapter.py`)는 호출마다 토큰버킷(15 req/s, burst
    15)을 통과한다 — `sleep`을 주입하지 않으면 30회 반복 중 burst를 넘는
    호출들이 진짜 `asyncio.sleep`으로 대기해 측정 대상(필터+정렬)과 무관한
    고정 지연이 섞여 배율이 기기 성능과 반비례로 널뛴다(task-3521 DC-12
    bitget throughput perf test와 동일 결함, base_adapter.py의 "시간은
    주입받는다" 설계를 그대로 따라 무지연 fake sleep을 준다)."""
    candles = _large_candle_set(_N_CANDLES)
    fake = _FakeKISAdapter({"005930": candles})

    async def _no_wait(_seconds: float) -> None:
        return None

    provider = KISProvider(fake, sleep=_no_wait)
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

    baseline_sample = perf_budget.sample(run_baseline)
    fetch_sample = await perf_budget.sample_async(run_fetch)

    assert baseline_sample.cpu_ms > 0.0
    ratio = fetch_sample.cpu_ms / baseline_sample.cpu_ms
    budget_ratio = 30.0  # fetch는 필터+정렬+CandleColumns 조립+비동기 호출
    # 오버헤드가 더해져 트리비얼 정렬 베이스라인보다 근본적으로 느리다.
    print(
        f"\nKISProvider.fetch_candles filter+sort throughput vs trivial baseline: "
        f"n_candles={_N_CANDLES} n_iterations={_N_ITERATIONS} "
        f"baseline={baseline_sample.cpu_ms:.1f}ms fetch={fetch_sample.cpu_ms:.1f}ms "
        f"ratio={ratio:.2f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"KISProvider.fetch_candles의 필터+정렬 파이프라인이 베이스라인 대비 "
        f"{ratio:.2f}배로 회귀했습니다(예산 {budget_ratio}배) — span 필터/정렬/"
        "CandleColumns 조립 경로에 의도치 않은 무거운 연산이 섞였을 가능성."
    )
