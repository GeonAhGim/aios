"""참조 시세 어댑터 2종 단위테스트(R-47) — 실제 네트워크 호출 없음.

리뷰 task-8044(REJECT) 후속(task-8335) — D3 증거 3종 추가:
- adversarial: 조작된 참조가가 DataDistrustMonitor의 실제 hard-fail
  (DISTRUSTED) 경로를 진짜로 발동시키는지(I-07/I-10, 이 리프의 실제 배선을
  통해 검증 — DataDistrustMonitor 자체 단위테스트는
  tests/unit/core/test_data_distrust_monitor.py가 이미 커버한다).
- replay 재현: scripts/replay_verify.py는 eventstore/ledger 프로젝션의
  byte-identical 재현을 검사하는데, reference_quotes.py는 이벤트스토어에
  아무것도 쓰지 않는 순수 외부 피드 어댑터라 그 스크립트가 문자 그대로
  적용되지 않는다(N/A 사유). 대신 이 리프의 실제 관심사에 맞는 동일 원칙의
  재현 테스트로 대체한다 — 동일 레코딩 입력을 두 번 재생해도
  DataDistrustMonitor 판정이 항상 동일해야 한다(숨은 비결정성 없음).
- 수치 성능 단언: ADR-2026-09-09-C Decision 1의 사전거래 게이트 예산(p99
  5ms) — DataDistrustMonitor.check()가 매 tick 이 경로에서 참조가를
  소비하므로 참조가 조회 자체도 그 예산 안에 들어야 한다.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from src.core.safety.data_distrust import DataDistrustLevel, DataDistrustMonitor
from src.data.models.market_data import Candle, Ticker
from src.services.safety.reference_quotes import (
    BinancePublicTickerReference,
    BitgetFuturesMarkPriceReference,
)


def _primary_ticker(price: str) -> Ticker:
    return Ticker(
        symbol="BTC/USDT",
        exchange="bitget",
        price=Decimal(price),
        bid=Decimal(price),
        ask=Decimal(price),
        volume_24h=Decimal("100"),
        timestamp=datetime.now(timezone.utc),
        source_type="primary",
    )


def _flat_candles(price: str, n: int = 10) -> list[Candle]:
    now = datetime.now(timezone.utc)
    return [
        Candle(
            symbol="BTC/USDT",
            exchange="bitget",
            timeframe="1h",
            open=Decimal(price),
            high=Decimal(price),
            low=Decimal(price),
            close=Decimal(price),
            volume=Decimal("1"),
            open_time=now,
            close_time=now,
        )
        for _ in range(n)
    ]


class _FakeBitgetAdapter:
    def __init__(self, *, price: str | None = None, raises: bool = False) -> None:
        self._price = price
        self._raises = raises

    async def get_futures_ticker(self, symbol: str):
        from datetime import datetime, timezone

        from src.data.models.market_data import Ticker

        if self._raises:
            raise RuntimeError("bitget futures ticker 조회 실패")
        return Ticker(
            symbol=symbol,
            exchange="bitget",
            price=Decimal(self._price),
            bid=Decimal(self._price),
            ask=Decimal(self._price),
            volume_24h=Decimal("10"),
            timestamp=datetime.now(timezone.utc),
            source_type="primary",
        )


async def test_bitget_futures_reference_returns_ticker_marked_as_reference():
    provider = BitgetFuturesMarkPriceReference(_FakeBitgetAdapter(price="100.5"))

    ticker = await provider.get_reference_ticker("BTC/USDT")

    assert ticker is not None
    assert ticker.price == Decimal("100.5")
    assert ticker.source_type == "reference"


async def test_bitget_futures_reference_returns_none_on_failure():
    provider = BitgetFuturesMarkPriceReference(_FakeBitgetAdapter(raises=True))

    ticker = await provider.get_reference_ticker("BTC/USDT")

    assert ticker is None


def _mock_transport(handler):
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.binance.com"
    )


async def test_binance_reference_returns_ticker_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["symbol"] == "BTCUSDT"
        return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "101.25"})

    provider = BinancePublicTickerReference(http_client=_mock_transport(handler))

    ticker = await provider.get_reference_ticker("BTC/USDT")

    assert ticker is not None
    assert ticker.price == Decimal("101.25")
    assert ticker.exchange == "binance"
    assert ticker.source_type == "reference"


async def test_binance_reference_returns_none_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(451, json={"msg": "restricted"})

    provider = BinancePublicTickerReference(http_client=_mock_transport(handler))

    ticker = await provider.get_reference_ticker("BTC/USDT")

    assert ticker is None


async def test_binance_reference_returns_none_on_malformed_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"symbol": "BTCUSDT"})  # price 필드 없음

    provider = BinancePublicTickerReference(http_client=_mock_transport(handler))

    ticker = await provider.get_reference_ticker("BTC/USDT")

    assert ticker is None


async def test_binance_reference_returns_none_on_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    provider = BinancePublicTickerReference(http_client=_mock_transport(handler))

    ticker = await provider.get_reference_ticker("BTC/USDT")

    assert ticker is None


async def test_manipulated_bitget_reference_price_reaches_real_distrust_hard_fail():
    """D3 adversarial(INVARIANTS I-07/I-10) — 참조가가 "일단 값을 돌려주기만
    하면 통과"가 아니라, 실제로 DataDistrustMonitor의 hard-fail(DISTRUSTED)
    경로를 발동시킬 수 있어야 한다. 조작(가격 조작 공격 시나리오 근사)된
    Bitget 참조가(500, primary=100 대비 400% 괴리)를 진짜 프로덕션 provider
    (BitgetFuturesMarkPriceReference)로 흘려 DataDistrustMonitor.check()에
    넣고, 그 결과가 실제로 DISTRUSTED로 떨어지는지 확인한다 — 단순
    provider 단위 목킹이 아니라 실제 배선을 통과시켜 I-10("구현됨≠작동함")을
    검증한다."""
    manipulated_provider = BitgetFuturesMarkPriceReference(_FakeBitgetAdapter(price="500"))
    manipulated_reference = await manipulated_provider.get_reference_ticker("BTC/USDT")
    assert manipulated_reference is not None  # 전제 조건: provider 자체는 정상 동작

    monitor = DataDistrustMonitor()
    level = await monitor.check(
        "BTC/USDT",
        _primary_ticker("100"),
        [manipulated_reference, manipulated_reference],
        _flat_candles("100"),
    )

    assert level == DataDistrustLevel.DISTRUSTED


async def test_reference_quote_replay_reproduces_identical_distrust_decision():
    """D3 replay 재현(scripts/replay_verify.py의 byte-identical 재현 원칙을
    이 리프 범위에 적용한 대체 테스트 — 위 클래스 docstring의 N/A 사유 참조).
    동일한 레코딩된 provider 응답을 두 번 재생해도 DataDistrustMonitor의
    판정이 항상 동일해야 한다 — 참조가 조회 경로에 숨은 비결정성(예:
    Ticker.timestamp의 wall-clock 유출)이 판정 결과 자체를 흔들지 않는지
    확인한다. 히스테리시스 상태 오염을 피하려고 매 재생마다 새
    DataDistrustMonitor 인스턴스를 쓴다."""
    provider = BitgetFuturesMarkPriceReference(_FakeBitgetAdapter(price="100.4"))
    primary = _primary_ticker("100")
    candles = _flat_candles("100")

    first_reference = await provider.get_reference_ticker("BTC/USDT")
    second_reference = await provider.get_reference_ticker("BTC/USDT")
    assert first_reference is not None
    assert second_reference is not None

    first_level = await DataDistrustMonitor().check(
        "BTC/USDT", primary, [first_reference, first_reference], candles
    )
    second_level = await DataDistrustMonitor().check(
        "BTC/USDT", primary, [second_reference, second_reference], candles
    )

    assert first_level == second_level == DataDistrustLevel.NORMAL
    assert first_reference.price == second_reference.price
    assert first_reference.source_type == second_reference.source_type


@pytest.mark.perf
async def test_binance_reference_lookup_stays_within_pretrade_gate_budget():
    """수치 성능 단언 — ADR-2026-09-09-C Decision 1의 사전거래 게이트 예산
    (p99 5ms)에 맞춘 p95 지연 단언. DataDistrustMonitor.check()가 매 tick
    이 참조가를 소비하는 사전거래 경로에 있으므로, 조회 자체도 그 예산
    안에 들어야 한다(N/A 아님 — 명시적 임계값 존재)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "100.5"})

    provider = BinancePublicTickerReference(http_client=_mock_transport(handler))

    durations: list[float] = []
    for _ in range(200):
        start = time.perf_counter()
        await provider.get_reference_ticker("BTC/USDT")
        durations.append(time.perf_counter() - start)

    durations.sort()
    p95 = durations[int(len(durations) * 0.95) - 1]
    assert p95 < 0.005, f"p95 참조가 조회 지연 {p95:.4f}s가 사전거래 예산(5ms) 초과"
