"""14번대 통합테스트 — /strategy-builder 라우터. 실제 FastAPI 앱 + 실제 dev DB.

실제 Bitget/KIS Demo 키가 없어 캔들 조회는 FastAPI dependency_overrides로
가짜 CredentialResolver를 주입해 검증한다(exchange_credentials 라우터
테스트와 동일 패턴)."""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient

from src.api.deps import get_pool
from src.api.service_deps import get_credential_resolver
from src.data.models.market_data import Candle
from src.main import app

STRONG_PASSWORD = "Str0ng!Passw0rd"


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
    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles

    async def get_adapter(self, user_id, exchange):
        return _FakeAdapter(self._candles)


_RISING_CLOSES = [100.0 + i for i in range(60)]


async def _override_resolver(pool=Depends(get_pool)):
    return _FakeResolver(_make_candles(_RISING_CLOSES))


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        app.dependency_overrides[get_credential_resolver] = _override_resolver
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
        app.dependency_overrides.pop(get_credential_resolver, None)


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


async def _register(client) -> dict:
    email = _unique_email()
    response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_list_indicators(client):
    response = await client.get("/strategy-builder/indicators")

    assert response.status_code == 200
    assert "RSI" in response.json()["indicators"]


async def test_compute_indicator(client):
    headers = await _register(client)

    response = await client.get(
        "/strategy-builder/indicators/SMA/compute",
        params={"exchange": "bitget", "symbol": "BTC/USDT", "timeframe": "1h", "period": 5},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["indicator"] == "SMA"
    assert body["params"]["timeperiod"] == 5
    assert any(v is not None for v in body["values"])


async def test_compute_indicator_unsupported_returns_400(client):
    headers = await _register(client)

    response = await client.get(
        "/strategy-builder/indicators/VWAP/compute",
        params={"exchange": "bitget", "symbol": "BTC/USDT"},
        headers=headers,
    )

    assert response.status_code == 400


async def test_create_strategy_compiles_and_saves(client):
    headers = await _register(client)
    strategy_id = f"editor-strategy-{uuid.uuid4().hex[:8]}"

    response = await client.post(
        "/strategy-builder/strategies",
        json={
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "target_asset": "BTC/USDT",
            "market": "crypto",
            "exchange": "bitget",
            "entry_conditions": [
                {"indicator": "RSI", "params": {}, "operator": "<", "threshold": 30}
            ],
            "exit_conditions": [
                {"indicator": "RSI", "params": {}, "operator": ">", "threshold": 70}
            ],
            "stop_loss_conditions": [
                {"indicator": "close", "params": {}, "operator": "<", "threshold": 90}
            ],
        },
        headers=headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "GENERATED"
    assert body["fsm_definition"]["strategy_id"] == strategy_id


async def test_create_strategy_rejects_non_whitelisted_asset(client):
    headers = await _register(client)

    response = await client.post(
        "/strategy-builder/strategies",
        json={
            "strategy_id": f"editor-strategy-{uuid.uuid4().hex[:8]}",
            "version": "1.0.0",
            "target_asset": "NOT/REAL",
            "market": "crypto",
            "exchange": "bitget",
            "entry_conditions": [
                {"indicator": "RSI", "params": {}, "operator": "<", "threshold": 30}
            ],
            "exit_conditions": [
                {"indicator": "RSI", "params": {}, "operator": ">", "threshold": 70}
            ],
            "stop_loss_conditions": [
                {"indicator": "close", "params": {}, "operator": "<", "threshold": 90}
            ],
        },
        headers=headers,
    )

    assert response.status_code == 400


async def test_get_strategy_owner_can_view(client):
    headers = await _register(client)
    strategy_id = f"editor-strategy-{uuid.uuid4().hex[:8]}"
    await client.post(
        "/strategy-builder/strategies",
        json={
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "target_asset": "BTC/USDT",
            "market": "crypto",
            "exchange": "bitget",
            "entry_conditions": [
                {"indicator": "RSI", "params": {}, "operator": "<", "threshold": 30}
            ],
            "exit_conditions": [
                {"indicator": "RSI", "params": {}, "operator": ">", "threshold": 70}
            ],
            "stop_loss_conditions": [
                {"indicator": "close", "params": {}, "operator": "<", "threshold": 90}
            ],
        },
        headers=headers,
    )

    response = await client.get(
        f"/strategy-builder/strategies/{strategy_id}/1.0.0", headers=headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "GENERATED"


async def test_get_strategy_stranger_gets_404(client):
    owner_headers = await _register(client)
    stranger_headers = await _register(client)
    strategy_id = f"editor-strategy-{uuid.uuid4().hex[:8]}"
    await client.post(
        "/strategy-builder/strategies",
        json={
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "target_asset": "BTC/USDT",
            "market": "crypto",
            "exchange": "bitget",
            "entry_conditions": [
                {"indicator": "RSI", "params": {}, "operator": "<", "threshold": 30}
            ],
            "exit_conditions": [
                {"indicator": "RSI", "params": {}, "operator": ">", "threshold": 70}
            ],
            "stop_loss_conditions": [
                {"indicator": "close", "params": {}, "operator": "<", "threshold": 90}
            ],
        },
        headers=owner_headers,
    )

    response = await client.get(
        f"/strategy-builder/strategies/{strategy_id}/1.0.0", headers=stranger_headers
    )

    assert response.status_code == 404


async def test_preview_returns_signals_and_disclaimer(client):
    headers = await _register(client)

    response = await client.post(
        "/strategy-builder/preview",
        json={
            "exchange": "bitget",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "limit": 60,
            "conditions": [
                {"indicator": "SMA", "params": {"timeperiod": 5}, "operator": ">", "threshold": 110}
            ],
        },
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert "정식 백테스트가 아닙니다" in body["disclaimer"]
    assert len(body["signal_indices"]) > 0


async def test_strategy_builder_requires_authentication(client):
    response = await client.post(
        "/strategy-builder/preview",
        json={"exchange": "bitget", "symbol": "BTC/USDT", "conditions": []},
    )

    assert response.status_code == 401
