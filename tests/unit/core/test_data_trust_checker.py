import time
from datetime import datetime, timezone
from decimal import Decimal

from src.core.parser.data_trust_checker import DataTrustChecker
from src.data.models.market_data import Ticker


def _ticker(price: str, symbol: str = "BTC/USDT") -> Ticker:
    return Ticker(
        symbol=symbol,
        exchange="bitget",
        price=Decimal(price),
        bid=Decimal(price),
        ask=Decimal(price),
        volume_24h=Decimal("100"),
        timestamp=datetime.now(timezone.utc),
        source_type="primary",
    )


async def test_no_reference_feed_skips_and_keeps_previous_state():
    checker = DataTrustChecker()
    is_distrusted = await checker.check(_ticker("100"), None)
    assert is_distrusted is False


async def test_small_deviation_stays_normal():
    checker = DataTrustChecker()
    is_distrusted = await checker.check(_ticker("100"), _ticker("100.5"))  # 0.5% 괴리
    assert is_distrusted is False


async def test_large_deviation_enters_distrust_and_publishes():
    published = []

    async def publish(topic, payload):
        published.append((topic, payload))

    checker = DataTrustChecker(publish=publish)
    is_distrusted = await checker.check(_ticker("102"), _ticker("100"))  # 2% 괴리 >= 1.5%

    assert is_distrusted is True
    assert len(published) == 1
    topic, payload = published[0]
    assert topic == "market.distrust.entered"
    assert payload["symbol"] == "BTC/USDT"
    assert Decimal(payload["deviation_pct"]) == Decimal("2")


async def test_distrust_does_not_exit_before_sustain_duration():
    checker = DataTrustChecker(exit_sustain_seconds=60.0)
    await checker.check(_ticker("102"), _ticker("100"))  # 진입
    # 괴리가 해제 임계치(0.75%) 아래로 내려갔지만 아직 60초 안 지남
    is_distrusted = await checker.check(_ticker("100.1"), _ticker("100"))
    assert is_distrusted is True


async def test_distrust_exits_after_sustained_low_deviation():
    checker = DataTrustChecker(exit_sustain_seconds=60.0)
    await checker.check(_ticker("102"), _ticker("100"))  # 진입

    # 시간이 흐른 것처럼 내부 상태를 직접 조작(실제 60초 대기 대신 결정적 테스트)
    await checker.check(_ticker("100.1"), _ticker("100"))  # 낮은 괴리 최초 관측
    checker._below_exit_since["BTC/USDT"] = time.monotonic() - 61.0

    published = []

    async def publish(topic, payload):
        published.append(topic)

    checker._publish = publish
    is_distrusted = await checker.check(_ticker("100.1"), _ticker("100"))

    assert is_distrusted is False
    assert "market.distrust.exited" in published


async def test_sustain_timer_resets_if_deviation_rises_again():
    checker = DataTrustChecker(exit_sustain_seconds=60.0)
    await checker.check(_ticker("102"), _ticker("100"))  # 진입
    await checker.check(_ticker("100.1"), _ticker("100"))  # 낮은 괴리 관측(카운터 시작)
    await checker.check(_ticker("102"), _ticker("100"))  # 다시 높은 괴리(카운터 리셋)

    assert "BTC/USDT" not in checker._below_exit_since
