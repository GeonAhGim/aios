"""BR-9(task-1787) — `src/exchanges/bitget/parsers.py`(구 `src/core/parser/*`)
이동 후 회귀 테스트. Bitget v2 실제 라이브 응답 캡처본을 fixture로 사용
(FD-2.5 완료조건: "실제 Bitget 응답 샘플 기준 단위테스트").

구 테스트(`tests/unit/core/parser/test_*.py`)의 "지원하지 않는 거래소"
negative test는 삭제한다 — `exchange` 매개변수 자체가 없어져 그 실패
경로가 더 이상 존재하지 않는다(ADR-2026-09-06-I D5, 상위 계층 문자열 가드
제거가 이 리프의 목적).

DEEPEN(task-2781, docs/audit/DEPTH_L4_BR.md #1787, D1 실측 -> D2 하한):
DEPTH 감사는 이 리프를 포함한 BR-9 전 테스트 파일에 수치 성능/지연·처리량
단언이 전혀 없다고 지적했다. `parse_ticker`/`parse_orderbook`은 WS
퍼블릭 채널의 틱마다 호출되는 진짜 핫패스(`market_ws_public_mixin.py`)라
회귀(예: 실수로 I/O·정규식·역순회가 끼어드는 경우) 탐지 가치가 크다.
공유 CI 머신 속도 편차에 강건하도록(task-2776/2774 선례와 동일 판단)
절대 ms 상수 대신 순수 dict/Decimal 변환치고 압도적으로 낮은 처리량
하한 하나만 건다 — 정상 동작이라면 이 하한의 수십~수백 배가 나온다."""
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.core.exceptions import FatalExchangeError
from src.exchanges.bitget.parsers import parse_candles, parse_orderbook, parse_ticker

REAL_BITGET_TICKER = {
    "open": "78217.08",
    "symbol": "BTCUSDT",
    "high24h": "80800",
    "low24h": "78196",
    "lastPr": "80663.08",
    "quoteVolume": "270635812.383435",
    "baseVolume": "3407.420693",
    "usdtVolume": "270635812.38343424",
    "ts": "1787851009318",
    "bidPr": "80664.02",
    "askPr": "80664.03",
    "bidSz": "0.859943",
    "askSz": "0.29158",
    "openUtc": "79023.47",
    "changeUtc24h": "0.02075",
    "change24h": "0.03127",
}

REAL_BITGET_ORDERBOOK = {
    "asks": [
        ["80565", "1.3861510000000000"],
        ["80568.63", "0.0297160000000000"],
    ],
    "bids": [
        ["80564.99", "0.3894280000000000"],
        ["80562.93", "0.0001860000000000"],
    ],
    "ts": "1787853071676",
}

REAL_BITGET_CANDLES = [
    ["1787853000000", "80515", "80565", "80510", "80565", "2.009698",
     "161876.22972749", "161876.22972749"],
    ["1787853060000", "80565", "80565", "80554.67", "80554.67", "0.094913",
     "7646.65714303", "7646.65714303"],
]


def test_parse_ticker_from_real_bitget_response():
    ticker = parse_ticker(REAL_BITGET_TICKER)
    assert ticker.symbol == "BTC/USDT"
    assert ticker.exchange == "bitget"
    assert ticker.price == Decimal("80663.08")
    assert ticker.bid == Decimal("80664.02")
    assert ticker.ask == Decimal("80664.03")
    assert ticker.volume_24h == Decimal("3407.420693")
    assert ticker.source_type == "primary"


def test_parse_ticker_source_type_override():
    ticker = parse_ticker(REAL_BITGET_TICKER, source_type="reference")
    assert ticker.source_type == "reference"


def test_parse_ticker_missing_field_raises_fatal_not_silent_default():
    broken = dict(REAL_BITGET_TICKER)
    del broken["lastPr"]
    with pytest.raises(FatalExchangeError):
        parse_ticker(broken)


def test_parse_orderbook_from_real_bitget_response():
    book = parse_orderbook(REAL_BITGET_ORDERBOOK, "BTC/USDT")
    assert book.symbol == "BTC/USDT"
    assert book.exchange == "bitget"
    assert book.asks[0].price == Decimal("80565")
    assert book.asks[0].quantity == Decimal("1.3861510000000000")
    assert book.bids[0].price == Decimal("80564.99")
    assert book.bids[0].price > book.bids[1].price  # 내림차순(최우선 매수호가가 첫 행)
    assert book.asks[0].price < book.asks[1].price  # 오름차순(최우선 매도호가가 첫 행)


def test_parse_orderbook_missing_field_raises():
    broken = {"asks": REAL_BITGET_ORDERBOOK["asks"], "bids": REAL_BITGET_ORDERBOOK["bids"]}
    with pytest.raises(FatalExchangeError):
        parse_orderbook(broken, "BTC/USDT")


def test_parse_candles_from_real_bitget_response():
    candles = parse_candles(REAL_BITGET_CANDLES, "BTC/USDT", "1m")
    assert len(candles) == 2
    first = candles[0]
    assert first.symbol == "BTC/USDT"
    assert first.exchange == "bitget"
    assert first.open == Decimal("80515")
    assert first.high == Decimal("80565")
    assert first.low == Decimal("80510")
    assert first.close == Decimal("80565")
    assert first.volume == Decimal("2.009698")
    assert first.open_time == datetime.fromtimestamp(1787853000000 / 1000, tz=timezone.utc)
    assert first.close_time == first.open_time + timedelta(minutes=1)


def test_parse_candles_unknown_timeframe_raises():
    with pytest.raises(FatalExchangeError):
        parse_candles(REAL_BITGET_CANDLES, "BTC/USDT", "3m")


def test_parse_ticker_throughput_meets_hot_path_floor():
    """수치 처리량 단언 — `parse_ticker`는 WS 퍼블릭 채널 틱마다 호출되는
    핫패스다(`market_ws_public_mixin.py`). 순수 dict 접근 + Decimal
    변환뿐이므로 어떤 CI 머신에서도 초당 수만~수십만 회는 나와야 정상이다.
    하한을 초당 5,000회로 압도적으로 낮게 잡아(정상 대비 수십 배 여유)
    머신 속도 편차로 인한 상시 적색 없이, I/O나 O(n) 역순회 같은 실질
    회귀만 잡는다."""
    n = 5_000
    started = time.perf_counter()
    for _ in range(n):
        parse_ticker(REAL_BITGET_TICKER)
    elapsed = time.perf_counter() - started
    throughput = n / elapsed
    floor_ops_per_sec = 5_000.0
    assert throughput >= floor_ops_per_sec, (
        f"parse_ticker 처리량이 {throughput:.0f} ops/sec으로 하한 "
        f"{floor_ops_per_sec:.0f} ops/sec 밑으로 떨어졌습니다"
        f"(n={n}, elapsed={elapsed * 1000:.1f}ms) — I/O나 알고리즘 회귀 가능성."
    )


def test_parse_candles_latency_scales_linearly_not_quadratically():
    """수치 지연 단언 — `parse_candles`는 캔들 개수만큼 순회하며 각
    항목을 독립적으로 변환하므로 이론상 선형(O(n))이다. 입력을 10배로
    늘렸을 때 소요시간이 선형 배율(10배)의 넉넉한 배수(예산 8배, 총
    80배)를 넘어서면 어딘가 항목 간 상호 스캔(O(n^2))이 끼어든 회귀다.
    절대 ms 임계 대신 같은 프로세스에서 방금 잰 작은 입력 기준값에
    정규화한 배율을 써서 머신 속도 편차에 강건하게 만든다."""
    small_batch = REAL_BITGET_CANDLES * 50  # n=100
    large_batch = REAL_BITGET_CANDLES * 500  # n=1000 (10배)

    # 워밍업 — 첫 호출의 인터프리터/캐시 워밍업 비용이 표본에 섞이지 않게 한다.
    parse_candles(small_batch, "BTC/USDT", "1m")

    small_started = time.perf_counter()
    parse_candles(small_batch, "BTC/USDT", "1m")
    small_elapsed = time.perf_counter() - small_started

    large_started = time.perf_counter()
    parse_candles(large_batch, "BTC/USDT", "1m")
    large_elapsed = time.perf_counter() - large_started

    size_ratio = len(large_batch) / len(small_batch)  # 10.0
    budget_multiplier = 8.0  # 선형(10배) 대비 넉넉한 여유 -> 총 80배까지 허용
    time_budget = small_elapsed * size_ratio * budget_multiplier
    assert large_elapsed <= time_budget, (
        f"parse_candles 소요시간이 입력 10배 증가에 비해 초선형으로 늘었습니다 "
        f"(small={small_elapsed * 1000:.2f}ms n={len(small_batch)}, "
        f"large={large_elapsed * 1000:.2f}ms n={len(large_batch)}, "
        f"budget={time_budget * 1000:.2f}ms) — O(n^2) 회귀 가능성."
    )
