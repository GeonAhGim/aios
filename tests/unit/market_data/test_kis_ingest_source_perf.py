"""LA-20 — kis_ingest_source 성능 검출력 검증 (task-16231).

검출력 확인: 원본 성능 단언(test_fetch_candles_repeated_calls_p95_latency_under_budget)이
예산을 10배 초과한 지연을 실제로 적색으로 만드는지 검증한다. 정상 입력에서 녹색인 것은
원본 테스트가 이미 커버하므로 여기서는 주입 적색만 확인한다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from src.exchanges.kis.adapter import KISAdapter
from src.foundation.market_data.adapters.kis_ingest_source import KisIngestSource
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from tests.support.perf_budget import PerfBudget

_TOKEN_RESPONSE = {"access_token": "tok-1", "access_token_token_expired": "2099-01-01 00:00:00"}


def _make_adapter_with_delay(*, delay_sec: float) -> KISAdapter:
    """지연이 주입된 어댑터 생성 — MockTransport 응답 전에 sleep(delay_sec)."""

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_TOKEN_RESPONSE)

        async def delayed_response() -> httpx.Response:
            await asyncio.sleep(delay_sec)
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg1": "ok",
                    "output2": [
                        {
                            "stck_bsop_date": "20260902",
                            "stck_oprc": "70000",
                            "stck_hgpr": "70100",
                            "stck_lwpr": "69900",
                            "stck_clpr": "70050",
                            "acml_vol": "1000",
                        }
                    ],
                },
            )

        # httpx.MockTransport는 동기 route만 받으므로,
        # 지연 시뮬레이션을 위해 async wrapper 대신 sync sleep 사용.
        import time

        time.sleep(delay_sec)
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "ok",
                "output2": [
                    {
                        "stck_bsop_date": "20260902",
                        "stck_oprc": "70000",
                        "stck_hgpr": "70100",
                        "stck_lwpr": "69900",
                        "stck_clpr": "70050",
                        "acml_vol": "1000",
                    }
                ],
            },
        )

    transport = httpx.MockTransport(route)
    client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )

    async def _noop_sleep(sec: float) -> None:
        await asyncio.sleep(sec)

    return KISAdapter(
        "app",
        "secret",
        "12345678",
        "01",
        is_paper_trading=True,
        http_client=client,
        sleep_fn=_noop_sleep,
    )


@pytest.mark.perf
async def test_fetch_candles_p95_latency_detection_power_10x_delay_goes_red(
    perf_budget: PerfBudget,
) -> None:
    """검출력 확인: 10배 지연(300ms)을 주입하면 p95가 30ms 예산을 넘어서 적색이 된다.

    원본 테스트의 budget_p95_ms=30.0ms에 대해 10배인 300ms 지연을 주입하고,
    같은 단언이 적색이 되는지 검증한다. — 테스트가 지연을 전혀 잡지 못하면
    이 테스트는 거짓 녹색(undetectable regression) 증거다.
    """
    source = KisIngestSource(_make_adapter_with_delay(delay_sec=0.3))
    start = datetime(2026, 9, 2, tzinfo=UTC)
    end = datetime(2026, 9, 3, tzinfo=UTC)
    await source.fetch_candles(Venue.KIS_KRX, "005930", Timeframe.D1, start, end)  # 토큰 캐시 예열

    n = 10
    budget_p95_ms = 30.0
    samples = await perf_budget.samples_async(
        lambda: source.fetch_candles(Venue.KIS_KRX, "005930", Timeframe.D1, start, end), n=n
    )
    wall_ms_values = sorted(s.wall_ms for s in samples)
    p95_idx = int(n * 0.95)
    p95_ms = wall_ms_values[p95_idx]
    print(
        f"[LA-20 fetch_candles 10x-delay] n={n} p95={p95_ms:.2f}ms (budget<{budget_p95_ms:.0f}ms)"
    )
    # 10배 지연 주입 시 p95가 예산을 넘어야 함 → AssertionError 발생이 정상
    with pytest.raises(AssertionError):
        assert p95_ms < budget_p95_ms, (
            f"예상: 10배 지연(300ms) 주입 시 p95가 예산을 넘어 적색. 실제: {p95_ms:.2f}ms"
        )
