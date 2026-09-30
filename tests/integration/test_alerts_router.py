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
        # raise_app_exceptions=False — PLT-20이 cancel_alert의 raw
        # HTTPException을 AlertNotFoundError로 교체했다. 도메인 예외는 이제
        # 전역 Exception 핸들러(ServerErrorMiddleware 승격)를 거치는데,
        # Starlette가 정상 응답 뒤에도 예외를 재전파하기 때문에 필요하다
        # (test_auth_router.py client 픽스처와 동일 근거).
        transport = ASGITransport(app=app, raise_app_exceptions=False)
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
    token = response.json()["data"]["access_token"]
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


async def test_create_alert_invalid_operator_rejected(client):
    headers = await _register(client)

    response = await client.post(
        "/alerts",
        json={
            "exchange": "bitget",
            "symbol": "BTC/USDT",
            "indicator": "RSI",
            "operator": "!=",  # not in condition_evaluation.Operator
            "threshold": 30,
        },
        headers=headers,
    )

    # RequestValidationError -> VALIDATION_INVALID_FIELD(400), not FastAPI's
    # default 422 (src/api/contracts/handlers.py::_handle_validation_error).
    assert response.status_code == 400


async def test_create_alert_missing_required_field_rejected(client):
    headers = await _register(client)

    response = await client.post(
        "/alerts",
        json={
            "exchange": "bitget",
            "symbol": "BTC/USDT",
            # indicator omitted
            "operator": "<",
            "threshold": 30,
        },
        headers=headers,
    )

    assert response.status_code == 400


async def test_create_alert_non_integer_params_rejected(client):
    headers = await _register(client)

    response = await client.post(
        "/alerts",
        json={
            "exchange": "bitget",
            "symbol": "BTC/USDT",
            "indicator": "RSI",
            "params": {"timeperiod": "not-an-int"},
            "operator": "<",
            "threshold": 30,
        },
        headers=headers,
    )

    assert response.status_code == 400


async def test_cancel_other_users_alert_returns_404(client):
    """소유권 불변식 — 다른 사용자의 알림은 취소할 수 없다(price_alerts의
    user_id 조건부 UPDATE가 0행을 반환해야 한다, standard-105)."""
    headers_owner = await _register(client)
    headers_attacker = await _register(client)
    create_response = await client.post(
        "/alerts",
        json={
            "exchange": "bitget",
            "symbol": "BTC/USDT",
            "indicator": "RSI",
            "operator": "<",
            "threshold": 30,
        },
        headers=headers_owner,
    )
    alert_id = create_response.json()["id"]

    response = await client.post(f"/alerts/{alert_id}/cancel", headers=headers_attacker)

    assert response.status_code == 404
    # invariant: the alert must still be ACTIVE for its real owner, not silently cancelled
    list_response = await client.get("/alerts", headers=headers_owner)
    owned = next(a for a in list_response.json() if a["id"] == alert_id)
    assert owned["status"] == "ACTIVE"


async def test_create_alert_active_limit_exceeded_rejected(client, monkeypatch):
    """실패주입 — Red team #24 상한(MAX_ACTIVE_ALERTS_PER_USER)을 0으로
    낮춰 DB에 50개를 실제로 만들지 않고 한도 초과 경로를 강제한다."""
    import src.services.alert_service as alert_service_module

    monkeypatch.setattr(alert_service_module, "MAX_ACTIVE_ALERTS_PER_USER", 0)
    headers = await _register(client)

    response = await client.post(
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

    assert response.status_code == 400


async def test_create_alert_dependency_failure_returns_500_not_silent_success(client, monkeypatch):
    """실패주입 — AlertService.create_alert이 예기치 못한 예외를 던지면
    500으로 표면화되어야 한다(fail-closed). 조용히 성공을 가장하거나
    빈 응답을 돌려주면 안 된다."""
    import src.services.alert_service as alert_service_module

    async def _boom(self, *args, **kwargs):
        raise RuntimeError("simulated dependency failure")

    monkeypatch.setattr(alert_service_module.AlertService, "create_alert", _boom)
    headers = await _register(client)

    response = await client.post(
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

    assert response.status_code == 500
