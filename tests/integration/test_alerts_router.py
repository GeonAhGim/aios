"""FD-14 통합테스트 — /alerts 라우터. 실제 FastAPI 앱 + 실제 dev DB."""
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


_FALLING_CLOSES = [200.0 - i for i in range(30)]


async def _override_resolver(pool=Depends(get_pool)):
    return _FakeResolver(_make_candles(_FALLING_CLOSES))


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


async def test_create_and_list_alert(client):
    headers = await _register(client)

    create_response = await client.post(
        "/alerts",
        json={
            "exchange": "bitget",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "indicator": "RSI",
            "params": {"timeperiod": 14},
            "operator": "<",
            "threshold": 30,
        },
        headers=headers,
    )
    assert create_response.status_code == 201
    assert create_response.json()["status"] == "ACTIVE"

    list_response = await client.get("/alerts", headers=headers)
    assert list_response.status_code == 200
    assert any(a["id"] == create_response.json()["id"] for a in list_response.json())


async def test_cancel_alert(client):
    headers = await _register(client)
    create_response = await client.post(
        "/alerts",
        json={
            "exchange": "bitget",
            "symbol": "BTC/USDT",
            "indicator": "RSI",
            "operator": "<",
            "threshold": 30,
        },
        headers=headers,
    )
    alert_id = create_response.json()["id"]

    response = await client.post(f"/alerts/{alert_id}/cancel", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"


async def test_cancel_nonexistent_alert_returns_404(client):
    headers = await _register(client)

    response = await client.post("/alerts/999999999/cancel", headers=headers)

    assert response.status_code == 404


async def test_list_alerts_excludes_other_users(client):
    headers_a = await _register(client)
    headers_b = await _register(client)
    create_response = await client.post(
        "/alerts",
        json={
            "exchange": "bitget",
            "symbol": "BTC/USDT",
            "indicator": "RSI",
            "operator": "<",
            "threshold": 30,
        },
        headers=headers_a,
    )
    alert_id = create_response.json()["id"]

    response = await client.get("/alerts", headers=headers_b)

    assert all(a["id"] != alert_id for a in response.json())


async def test_alerts_require_authentication(client):
    response = await client.get("/alerts")

    assert response.status_code == 401
