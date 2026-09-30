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


# ── Negative tests (3건 이상: 불변식 위반 입력을 명시적으로 거부) ──────────────


async def test_start_validation_rejects_missing_exchange_field(client):
    """StartValidationRequest.exchange는 필수 필드 — 누락 시 pydantic
    RequestValidationError가 VALIDATION_INVALID_FIELD -> 400으로 매핑된다
    (handlers.py _handle_validation_error, error_codes.py HTTP_STATUS)."""
    headers, owner_id = await _register(client)
    strategy_id = f"test-strategy-{uuid.uuid4().hex[:8]}"

    body_without_exchange = {k: v for k, v in _REQUEST_BODY.items() if k != "exchange"}
    response = await client.post(
        f"/v1/foundation/validation-runs/{strategy_id}/1.0.0",
        json=body_without_exchange,
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_start_validation_rejects_non_numeric_cost_model_fee_bps(client):
    """cost_model_fee_bps는 Decimal 파싱 가능한 값이어야 한다 — 숫자로
    변환 불가능한 문자열이면 같은 400/VALIDATION_INVALID_FIELD 경로를 탄다."""
    headers, owner_id = await _register(client)
    strategy_id = f"test-strategy-{uuid.uuid4().hex[:8]}"

    body_with_invalid_fee = {**_REQUEST_BODY, "cost_model_fee_bps": "not-a-number"}
    response = await client.post(
        f"/v1/foundation/validation-runs/{strategy_id}/1.0.0",
        json=body_with_invalid_fee,
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_start_validation_rejects_nonexistent_strategy(client):
    """존재하지 않는 strategy_id/version 조합은 StrategyBuilderService.get_strategy가
    StrategyLifecycleError를 던지고, start_validation()이 이를
    StrategyNotEligibleForValidationError로 감싼다 —
    STATE_INVALID_TRANSITION -> 409(exception_registry_foundation.py:247).
    9.9 절대원칙: 없는 전략은 아예 BACKTESTING 상태가 아니므로 같은 상태
    불변식 위반 경로로 거부된다."""
    headers, owner_id = await _register(client)
    strategy_id = f"test-strategy-never-created-{uuid.uuid4().hex[:8]}"

    response = await client.post(
        f"/v1/foundation/validation-runs/{strategy_id}/1.0.0",
        json=_REQUEST_BODY,
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "STATE_INVALID_TRANSITION"


# ── Failure injection test (1건 이상: 의존성 예외 유발) ──────────────────────


async def test_start_validation_credential_lookup_failure_returns_404_not_500(client, pool):
    """CredentialResolver.get_adapter가 CredentialNotFoundError를 던지면
    (예: 저장된 거래소 자격증명이 없거나 해지된 경우), 라우터가 이를 삼켜
    500으로 누수시키지 않고 EXCEPTION_MAP(RESOURCE_NOT_FOUND -> 404)을 통해
    정상적으로 변환해야 한다(exception_registry.py:109)."""
    from src.services.credential_resolver import CredentialNotFoundError

    headers, owner_id = await _register(client)
    strategy_id = f"test-strategy-{uuid.uuid4().hex[:8]}"
    await _create_strategy_in_backtesting(pool, owner_id, strategy_id)

    class _FailingResolver:
        async def get_adapter(self, user_id, exchange):
            raise CredentialNotFoundError(f"{exchange} 자격증명이 없거나 해지되었습니다.")

    async def _override_failing_resolver():
        return _FailingResolver()

    app.dependency_overrides[get_credential_resolver] = _override_failing_resolver
    try:
        response = await client.post(
            f"/v1/foundation/validation-runs/{strategy_id}/1.0.0",
            json=_REQUEST_BODY,
            headers=headers,
        )
    finally:
        app.dependency_overrides[get_credential_resolver] = _override_resolver

    assert response.status_code == 404
    body = response.json()
    assert body["error_code"] == "RESOURCE_NOT_FOUND"
