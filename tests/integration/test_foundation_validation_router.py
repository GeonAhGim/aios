"""FND-04 통합테스트 — /v1/foundation/validation-runs 라우터. 실제 FastAPI 앱
+ 실제 dev DB. 실제 거래소 키가 없어 캔들 조회는 fake CredentialResolver로
주입한다(test_strategy_builder_router.py와 동일 패턴)."""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values
from fastapi import Depends
from httpx import ASGITransport, AsyncClient

from src.api.deps import get_pool
from src.api.service_deps import get_credential_resolver
from src.data.models.market_data import Candle
from src.main import app
from src.services.strategy_builder_service import StrategyBuilderService

STRONG_PASSWORD = "Str0ng!Passw0rd"


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


def _flat_candles(count: int = 30) -> list[Candle]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        Candle(
            symbol="BTC/USDT",
            exchange="bitget",
            timeframe="1h",
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=Decimal("1"),
            open_time=base + timedelta(hours=i),
            close_time=base + timedelta(hours=i, minutes=59),
        )
        for i in range(count)
    ]


class _FakeAdapter:
    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles

    async def get_ohlcv(self, symbol, timeframe, limit=100):
        return self._candles[-limit:]


class _FakeResolver:
    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles

    async def get_adapter(self, user_id, exchange):
        return _FakeAdapter(self._candles)


async def _override_resolver(pool=Depends(get_pool)):
    return _FakeResolver(_flat_candles())


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        app.dependency_overrides[get_credential_resolver] = _override_resolver
        # raise_app_exceptions=False — task-1218이 validation.py의 raw
        # HTTPException을 도메인 예외로 교체했다(이유는 tests/integration/
        # test_auth_router.py의 client 픽스처와 동일).
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
        app.dependency_overrides.pop(get_credential_resolver, None)


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


async def _register(client) -> tuple[dict, str]:
    email = _unique_email()
    response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/users/me", headers=headers)
    return headers, me.json()["data"]["user_id"]


_FSM_NEVER_FIRES = {
    "strategy_id": "placeholder",
    "version": "1.0.0",
    "target_asset": "BTC/USDT",
    "market": "crypto",
    "exchange": "bitget",
    "initial_state": "IDLE",
    "states": ["IDLE", "BUY_ORDER_PENDING"],
    "transitions": [
        # RSI는 정의상 [0,100] 범위라 이 조건은 실제 TA-Lib 지표로도 절대
        # 참이 될 수 없다 — 라우터 테스트는 fake indicator_service를 주입할
        # 수 없어(HTTP 경로가 항상 실제 IndicatorService를 씀) 진짜 지표
        # 이름으로 "절대 안 켜지는 신호"를 만들어야 한다.
        {"from_state": "IDLE", "to_state": "BUY_ORDER_PENDING", "condition": "RSI > 1000"}
    ],
    "author_agent": "test",
}


async def _create_strategy_in_backtesting(pool, owner_id: str, strategy_id: str) -> None:
    service = StrategyBuilderService(pool)
    fsm_definition = {**_FSM_NEVER_FIRES, "strategy_id": strategy_id}
    await service.save_strategy(
        uuid.UUID(owner_id),
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition=fsm_definition,
    )
    await service.transition_lifecycle(strategy_id, "1.0.0", "BACKTESTING")


_REQUEST_BODY = {
    "exchange": "bitget",
    "symbol": "BTC/USDT",
    "timeframe": "1h",
    "limit": 30,
    "cost_model_fee_bps": "5",
    "cost_model_slippage_bps": "2",
    "warmup_bars": 0,
    "periods_per_year": 252,
    "initial_equity": "10000",
}


async def test_start_validation_requires_authentication(client):
    response = await client.post(
        "/v1/foundation/validation-runs/some-strategy/1.0.0", json=_REQUEST_BODY
    )
    assert response.status_code == 401


async def test_start_validation_on_generated_strategy_is_409(client, pool):
    headers, owner_id = await _register(client)
    strategy_id = f"test-strategy-{uuid.uuid4().hex[:8]}"
    service = StrategyBuilderService(pool)
    await service.save_strategy(
        uuid.UUID(owner_id),
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={**_FSM_NEVER_FIRES, "strategy_id": strategy_id},
    )

    response = await client.post(
        f"/v1/foundation/validation-runs/{strategy_id}/1.0.0",
        json=_REQUEST_BODY,
        headers=headers,
    )
    assert response.status_code == 409


async def test_start_validation_succeeds_and_advances_lifecycle(client, pool):
    headers, owner_id = await _register(client)
    strategy_id = f"test-strategy-{uuid.uuid4().hex[:8]}"
    await _create_strategy_in_backtesting(pool, owner_id, strategy_id)

    response = await client.post(
        f"/v1/foundation/validation-runs/{strategy_id}/1.0.0",
        json=_REQUEST_BODY,
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["state"] == "SUCCEEDED"
    assert body["outcome"] in ("PASS", "PASS_WITH_OBLIGATIONS")

    strategy_response = await client.get(
        f"/strategy-builder/strategies/{strategy_id}/1.0.0", headers=headers
    )
    assert strategy_response.json()["status"] == "VALIDATING"
