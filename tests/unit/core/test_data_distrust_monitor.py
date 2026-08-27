import time
from datetime import datetime, timezone
from decimal import Decimal

from src.core.safety.data_distrust import DataDistrustLevel, DataDistrustMonitor
from src.data.models.market_data import Candle, Ticker


def _ticker(price: str) -> Ticker:
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


def _flat_candles(price: str, n: int = 5) -> list[Candle]:
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


async def test_quorum_not_met_returns_suspicious():
    monitor = DataDistrustMonitor()
    level = await monitor.check("BTC/USDT", _ticker("100"), [None], _flat_candles("100"))
    assert level == DataDistrustLevel.SUSPICIOUS


async def test_all_sources_agree_stays_normal():
    monitor = DataDistrustMonitor()
    level = await monitor.check(
        "BTC/USDT", _ticker("100"), [_ticker("100.1"), _ticker("99.9")], _flat_candles("100")
    )
    assert level == DataDistrustLevel.NORMAL


async def test_single_skewed_reference_does_not_trigger_false_positive():
    # 3소스 중 1개(reference 하나)만 크게 왜곡 — 중앙값은 정상 유지되어야 함
    monitor = DataDistrustMonitor()
    level = await monitor.check(
        "BTC/USDT", _ticker("100"), [_ticker("100.2"), _ticker("150")], _flat_candles("100")
    )
    assert level == DataDistrustLevel.NORMAL


async def test_majority_deviation_triggers_distrust():
    # 2개 소스(과반)가 primary와 크게 다른 값에서 서로 합의 -> 중앙값이 끌려감
    monitor = DataDistrustMonitor()
    level = await monitor.check(
        "BTC/USDT", _ticker("100"), [_ticker("150"), _ticker("151")], _flat_candles("100")
    )
    assert level == DataDistrustLevel.DISTRUSTED


async def test_statistical_check_flags_abnormal_jump_even_if_feeds_agree():
    monitor = DataDistrustMonitor(volatility_multiplier=Decimal("5"))
    # 캔들은 완전히 평평(변동성 0에 가까움)한데 현재가가 크게 튐 -> 통계적 이상
    flat = _flat_candles("100", n=10)
    level = await monitor.check(
        "BTC/USDT", _ticker("120"), [_ticker("120"), _ticker("120")], flat
    )
    assert level == DataDistrustLevel.DISTRUSTED


async def test_distrust_does_not_exit_before_sustain_duration():
    monitor = DataDistrustMonitor(exit_sustain_seconds=60.0)
    await monitor.check("BTC/USDT", _ticker("150"), [_ticker("100"), _ticker("100")], [])
    assert monitor.current_level("BTC/USDT") == DataDistrustLevel.DISTRUSTED

    level = await monitor.check(
        "BTC/USDT", _ticker("100.1"), [_ticker("100"), _ticker("100")], []
    )
    assert level == DataDistrustLevel.DISTRUSTED  # 아직 60초 안 지남


async def test_distrust_exits_after_sustained_low_deviation():
    monitor = DataDistrustMonitor(exit_sustain_seconds=60.0)
    await monitor.check("BTC/USDT", _ticker("150"), [_ticker("100"), _ticker("100")], [])

    await monitor.check("BTC/USDT", _ticker("100.1"), [_ticker("100"), _ticker("100")], [])
    monitor._below_exit_since["BTC/USDT"] = time.monotonic() - 61.0

    published = []

    async def publish(topic, payload):
        published.append(payload)

    monitor._publish = publish
    level = await monitor.check(
        "BTC/USDT", _ticker("100.1"), [_ticker("100"), _ticker("100")], []
    )

    assert level == DataDistrustLevel.NORMAL
    assert published[-1]["level"] == "NORMAL"


async def test_publish_called_only_on_transition():
    published = []

    async def publish(topic, payload):
        published.append(payload)

    monitor = DataDistrustMonitor(publish=publish)
    await monitor.check("BTC/USDT", _ticker("100"), [_ticker("100"), _ticker("100")], [])
    await monitor.check("BTC/USDT", _ticker("100"), [_ticker("100"), _ticker("100")], [])

    assert published == []  # 상태 변화 없었으므로(계속 NORMAL) 발행 안 됨
