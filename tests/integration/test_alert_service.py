"""FD-14 통합테스트 — 실제 dev DB 대상 (AlertService)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.data.models.market_data import Candle
from src.services.alert_service import AlertError, AlertService
from src.services.credential_resolver import CredentialNotFoundError
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


def _make_candles(closes: list[float]) -> list[Candle]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []
    for i, close in enumerate(closes):
        open_time = base + timedelta(hours=i)
        candles.append(
            Candle(
                symbol="BTC/USDT",
                exchange="bitget",
                timeframe="1h",
                open=Decimal(str(close)),
                high=Decimal(str(close + 1)),
                low=Decimal(str(close - 1)),
                close=Decimal(str(close)),
                volume=Decimal("100"),
                open_time=open_time,
                close_time=open_time + timedelta(hours=1),
            )
        )
    return candles


class _FakeAdapter:
    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles

    async def get_ohlcv(self, symbol, timeframe, limit=100):
        return self._candles[-limit:]


class _FakeResolver:
    def __init__(self, candles: list[Candle] | None = None) -> None:
        self._candles = candles

    async def get_adapter(self, user_id, exchange):
        if self._candles is None:
            raise CredentialNotFoundError("등록된 자격증명이 없습니다.")
        return _FakeAdapter(self._candles)


_FALLING_CLOSES = [200.0 - i for i in range(30)]  # RSI가 과매도로 떨어지는 흐름


@pytest.fixture
def service(pool):
    return AlertService(pool, credential_resolver=_FakeResolver(_make_candles(_FALLING_CLOSES)))


async def test_create_alert_starts_active(service, pool):
    user = await create_test_user(pool)

    alert = await service.create_alert(
        user,
        exchange="bitget",
        symbol="BTC/USDT",
        timeframe="1h",
        indicator="RSI",
        params={"timeperiod": 14},
        operator="<",
        threshold=30.0,
    )

    assert alert.status == "ACTIVE"
    assert alert.user_id == user


async def test_list_my_alerts_returns_only_own_alerts(service, pool):
    owner = await create_test_user(pool)
    other = await create_test_user(pool)
    await service.create_alert(
        owner,
        exchange="bitget",
        symbol="BTC/USDT",
        timeframe="1h",
        indicator="RSI",
        params={},
        operator="<",
        threshold=30.0,
    )

    owner_alerts = await service.list_my_alerts(owner)
    other_alerts = await service.list_my_alerts(other)

    assert len(owner_alerts) == 1
    assert other_alerts == []


async def test_cancel_alert_marks_cancelled(service, pool):
    user = await create_test_user(pool)
    alert = await service.create_alert(
        user,
        exchange="bitget",
        symbol="BTC/USDT",
        timeframe="1h",
        indicator="RSI",
        params={},
        operator="<",
        threshold=30.0,
    )

    cancelled = await service.cancel_alert(user, alert.id)

    assert cancelled.status == "CANCELLED"


async def test_cancel_alert_rejects_other_users_alert(service, pool):
    owner = await create_test_user(pool)
    stranger = await create_test_user(pool)
    alert = await service.create_alert(
        owner,
        exchange="bitget",
        symbol="BTC/USDT",
        timeframe="1h",
        indicator="RSI",
        params={},
        operator="<",
        threshold=30.0,
    )

    with pytest.raises(AlertError):
        await service.cancel_alert(stranger, alert.id)


async def test_create_alert_rejects_over_per_user_cap(service, pool, monkeypatch):
    """레드팀 #24 — evaluate_all_active()가 전체 알림을 순차 for 루프로
    돌기 때문에, 한 사용자가 알림을 무한정 생성하면 다른 모든 사용자의
    평가 주기가 함께 늘어진다. 사용자당 ACTIVE 알림 개수 상한을 실제로
    막는지 확인."""
    import src.services.alert_service as alert_service_module

    monkeypatch.setattr(alert_service_module, "MAX_ACTIVE_ALERTS_PER_USER", 2)
    user = await create_test_user(pool)

    for _ in range(2):
        await service.create_alert(
            user,
            exchange="bitget",
            symbol="BTC/USDT",
            timeframe="1h",
            indicator="RSI",
            params={},
            operator="<",
            threshold=30.0,
        )

    with pytest.raises(AlertError):
        await service.create_alert(
            user,
            exchange="bitget",
            symbol="BTC/USDT",
            timeframe="1h",
            indicator="RSI",
            params={},
            operator="<",
            threshold=30.0,
        )


async def test_evaluate_all_active_triggers_matching_alert(pool):
    user = await create_test_user(pool)
    service = AlertService(
        pool, credential_resolver=_FakeResolver(_make_candles(_FALLING_CLOSES))
    )
    alert = await service.create_alert(
        user,
        exchange="bitget",
        symbol="BTC/USDT",
        timeframe="1h",
        indicator="RSI",
        params={"timeperiod": 14},
        operator="<",
        threshold=50.0,
    )

    triggered = await service.evaluate_all_active()

    assert any(a.id == alert.id for a in triggered)
    remaining = await service.list_my_alerts(user)
    updated = next(a for a in remaining if a.id == alert.id)
    assert updated.status == "TRIGGERED"
    assert updated.triggered_value is not None
    assert updated.triggered_at is not None


async def test_evaluate_all_active_skips_non_matching_alert(pool):
    user = await create_test_user(pool)
    service = AlertService(
        pool, credential_resolver=_FakeResolver(_make_candles(_FALLING_CLOSES))
    )
    alert = await service.create_alert(
        user,
        exchange="bitget",
        symbol="BTC/USDT",
        timeframe="1h",
        indicator="RSI",
        params={"timeperiod": 14},
        operator=">",
        threshold=99.0,  # 절대 도달하지 않을 임계값
    )

    triggered = await service.evaluate_all_active()

    assert all(a.id != alert.id for a in triggered)
    remaining = await service.list_my_alerts(user)
    updated = next(a for a in remaining if a.id == alert.id)
    assert updated.status == "ACTIVE"


async def test_evaluate_all_active_survives_bad_indicator_and_still_evaluates_others(pool):
    """docs/RED_TEAM_FINDINGS.md #21 회귀 — 미검증 indicator/params가
    calculate()에서 예외를 던지면 원래는 evaluate_all_active() 루프
    전체가 죽어(그 루프를 감싼 백그라운드 태스크까지) 재시작 전까지
    어떤 사용자의 알림도 다시는 평가되지 않았다. 존재하지 않는 지표명
    알림 하나가 있어도 (1) evaluate_all_active() 자체가 예외 없이
    끝나고, (2) 그 뒤로도 서비스가 여전히 정상 작동해 다른 정상 알림을
    평가할 수 있어야 한다."""
    user = await create_test_user(pool)
    service = AlertService(
        pool, credential_resolver=_FakeResolver(_make_candles(_FALLING_CLOSES))
    )
    bad_alert = await service.create_alert(
        user,
        exchange="bitget",
        symbol="BTC/USDT",
        timeframe="1h",
        indicator="NOT_A_REAL_INDICATOR",
        params={},
        operator="<",
        threshold=50.0,
    )

    # 예외 없이 끝나야 한다 — 원래는 여기서 IndicatorError/TypeError가
    # evaluate_all_active() 밖으로 그대로 튀어나갔다.
    triggered = await service.evaluate_all_active()
    assert all(a.id != bad_alert.id for a in triggered)

    remaining = await service.list_my_alerts(user)
    still_active = next(a for a in remaining if a.id == bad_alert.id)
    assert still_active.status == "ACTIVE"  # 건너뛴 것이지 잘못 처리된 게 아님

    # 루프가 죽지 않았으니, 같은 서비스로 그 뒤에 만든 정상 알림도 여전히
    # 평가돼야 한다.
    good_alert = await service.create_alert(
        user,
        exchange="bitget",
        symbol="BTC/USDT",
        timeframe="1h",
        indicator="RSI",
        params={"timeperiod": 14},
        operator="<",
        threshold=50.0,
    )

    triggered_again = await service.evaluate_all_active()
    assert any(a.id == good_alert.id for a in triggered_again)


async def test_evaluate_all_active_skips_alert_without_credentials(pool):
    user = await create_test_user(pool)
    service = AlertService(pool, credential_resolver=_FakeResolver(None))
    alert = await service.create_alert(
        user,
        exchange="bitget",
        symbol="BTC/USDT",
        timeframe="1h",
        indicator="RSI",
        params={},
        operator="<",
        threshold=99.0,
    )

    triggered = await service.evaluate_all_active()

    assert all(a.id != alert.id for a in triggered)


async def test_evaluate_all_active_ignores_cancelled_alerts(pool):
    user = await create_test_user(pool)
    service = AlertService(
        pool, credential_resolver=_FakeResolver(_make_candles(_FALLING_CLOSES))
    )
    alert = await service.create_alert(
        user,
        exchange="bitget",
        symbol="BTC/USDT",
        timeframe="1h",
        indicator="RSI",
        params={"timeperiod": 14},
        operator="<",
        threshold=50.0,
    )
    await service.cancel_alert(user, alert.id)

    triggered = await service.evaluate_all_active()

    assert all(a.id != alert.id for a in triggered)


async def test_evaluate_all_active_publishes_event(pool):
    user = await create_test_user(pool)
    published: list[tuple[str, dict]] = []

    async def _publish(topic, payload):
        published.append((topic, payload))

    service = AlertService(
        pool,
        credential_resolver=_FakeResolver(_make_candles(_FALLING_CLOSES)),
        publish=_publish,
    )
    await service.create_alert(
        user,
        exchange="bitget",
        symbol="BTC/USDT",
        timeframe="1h",
        indicator="RSI",
        params={"timeperiod": 14},
        operator="<",
        threshold=50.0,
    )

    await service.evaluate_all_active()

    assert any(topic == "alert.triggered" for topic, _ in published)
