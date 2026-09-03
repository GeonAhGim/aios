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
        # raise_app_exceptions=False — PLT-18 이후 라우터가 도메인 예외를 그대로
        # 던지고 전역 Exception 핸들러(src/api/contracts/handlers.py)가 변환한다.
        # test_exchange_credentials_router.py client 픽스처와 동일 근거
        # (Starlette가 정상 응답 뒤에도 예외를 재전파한다).
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


async def test_get_candles_returns_ohlcv(client):
    headers = await _register(client)

    response = await client.get(
        "/strategy-builder/candles",
        params={"exchange": "bitget", "symbol": "BTC/USDT", "timeframe": "1h", "limit": 10},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == len(_RISING_CLOSES[-10:])
    assert set(body[0].keys()) == {"open_time", "open", "high", "low", "close", "volume"}


async def test_get_candles_requires_authentication(client):
    response = await client.get(
        "/strategy-builder/candles",
        params={"exchange": "bitget", "symbol": "BTC/USDT"},
    )

    assert response.status_code == 401


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


async def test_list_strategies_returns_only_own_strategies(client):
    owner_headers = await _register(client)
    other_headers = await _register(client)
    strategy_id = f"editor-strategy-{uuid.uuid4().hex[:8]}"
    other_strategy_id = f"editor-strategy-{uuid.uuid4().hex[:8]}"
    body_template = {
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
    }
    await client.post(
        "/strategy-builder/strategies",
        json={**body_template, "strategy_id": strategy_id},
        headers=owner_headers,
    )
    await client.post(
        "/strategy-builder/strategies",
        json={**body_template, "strategy_id": other_strategy_id},
        headers=other_headers,
    )

    response = await client.get("/strategy-builder/strategies", headers=owner_headers)

    assert response.status_code == 200
    ids = [item["strategy_id"] for item in response.json()]
    assert strategy_id in ids
    assert other_strategy_id not in ids


async def test_wizard_generates_conditions(client):
    headers = await _register(client)

    response = await client.post(
        "/strategy-builder/wizard",
        json={"goal": "STEADY_GROWTH", "risk_tolerance": "MEDIUM"},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["entry_conditions"]
    assert body["exit_conditions"]
    assert body["stop_loss_conditions"]
    assert body["explanation"]


async def test_wizard_rejects_unknown_goal(client):
    headers = await _register(client)

    response = await client.post(
        "/strategy-builder/wizard",
        json={"goal": "NOT_A_GOAL", "risk_tolerance": "MEDIUM"},
        headers=headers,
    )

    assert response.status_code == 400


async def test_wizard_output_can_be_saved_as_a_strategy(client):
    """마법사 결과물을 그대로 기존 전략 저장 엔드포인트에 넣어도
    작동한다는 것 — 별도 저장 경로를 새로 만들지 않은 설계 확인."""
    headers = await _register(client)
    wizard_response = await client.post(
        "/strategy-builder/wizard",
        json={"goal": "AGGRESSIVE_GROWTH", "risk_tolerance": "HIGH"},
        headers=headers,
    )
    generated = wizard_response.json()
    strategy_id = f"wizard-strategy-{uuid.uuid4().hex[:8]}"

    response = await client.post(
        "/strategy-builder/strategies",
        json={
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "target_asset": "BTC/USDT",
            "market": "crypto",
            "exchange": "bitget",
            "entry_conditions": generated["entry_conditions"],
            "exit_conditions": generated["exit_conditions"],
            "stop_loss_conditions": generated["stop_loss_conditions"],
            "entry_combine": generated["entry_combine"],
            "exit_combine": generated["exit_combine"],
            "stop_loss_combine": generated["stop_loss_combine"],
        },
        headers=headers,
    )

    assert response.status_code == 201
    assert response.json()["fsm_definition"]["strategy_id"] == strategy_id


async def test_generate_from_prompt_is_not_yet_available(client):
    headers = await _register(client)

    response = await client.post(
        "/strategy-builder/generate-from-prompt",
        json={"prompt": "RSI 과매도에서 반등 매수하는 전략 만들어줘"},
        headers=headers,
    )

    assert response.status_code == 501


async def test_strategy_builder_requires_authentication(client):
    response = await client.post(
        "/strategy-builder/preview",
        json={"exchange": "bitget", "symbol": "BTC/USDT", "conditions": []},
    )

    assert response.status_code == 401
