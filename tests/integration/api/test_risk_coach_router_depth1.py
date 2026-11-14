"""U-8 DEEPEN-DEPTH D2 보강: risk-coach position-size 라우터의 음성/실패주입/성능/게이트 적색 증거.

기존 test_risk_coach_router.py는 수정하지 않고 같은 fixture 구성을 따른다.
D3 replay_verify N/A(주문/원장 이벤트를 만들지 않는 계산 API).
성능 예산 _BUDGET_MS: 요청 1건(인증 포함, 인메모리) best-of-5 CPU 시간 기준.
로컬 실측 best-of-5는 _MEASURED_MS(ms) 안팎이며 예산은 실측의 30배 이상 여유다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api import deps
from src.api.contracts.handlers import install_exception_handlers
from src.api.routers import risk_coach
from src.core.portfolio.config import PortfolioConfig
from src.core.portfolio.sizing.selector import size_for
from src.core.portfolio.state_input import PortfolioStateInput
from src.services.auth.session_repository import Session
from src.services.auth.tokens import TokenIssuer
from src.services.auth_service import User
from tests.support import perf_inject
from tests.support.perf_budget import PerfBudget

USER_ID = uuid.UUID(int=8107)
SESSION_ID = uuid.UUID(int=8108)
PATH = "/risk-coach/position-size"
_MEASURED_MS = 5.0
_BUDGET_MS = 200.0
_COST_MODEL = {"model_id": "cm-1", "cost_model_hash": "0" * 64}


def _config() -> dict:
    return {
        "method": "FIXED_FRACTIONAL",
        "fraction_pct": "10",
        "rebalance_band_pct": "5",
        "min_trade_notional": "1",
        "cost_model": _COST_MODEL,
    }


def _state_input(**overrides: object) -> dict:
    base = {
        "allocated_capital": "1000",
        "position_quantity": "0",
        "current_price": "100",
        "total_equity": "10000",
        "cash_available": "9000",
        "portfolio_config": _config(),
    }
    base.update(overrides)
    return base


def _body(**state_overrides: object) -> dict:
    return {"config": _config(), "state_input": _state_input(**state_overrides)}


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FF_U8_RISK_COACH", "1")


@pytest.fixture
async def client(monkeypatch):
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(risk_coach.router, prefix="/risk-coach")
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=object())
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    app.dependency_overrides[deps.get_pool] = lambda: pool
    user = User(
        user_id=USER_ID,
        email="risk-coach@example.test",
        display_name=None,
        mfa_enabled=False,
        mfa_verified_at=None,
        status="ACTIVE",
        is_verifier=False,
        is_platform_admin=False,
    )
    monkeypatch.setattr(deps, "get_user_by_id", AsyncMock(return_value=user))
    now = datetime.now(UTC)
    session = Session(
        id=SESSION_ID,
        user_id=USER_ID,
        tenant_id=USER_ID,
        refresh_hash="0" * 64,
        auth_level="PASSWORD",
        issued_at=now,
        rotated_at=None,
        expires_at=now + timedelta(hours=1),
        revoked_at=None,
        revoke_reason=None,
    )
    monkeypatch.setattr(deps.session_repository, "get_active", AsyncMock(return_value=session))
    monkeypatch.setattr("asyncpg.connect", MagicMock(side_effect=AssertionError("real DB")))
    monkeypatch.setattr("asyncpg.create_pool", MagicMock(side_effect=AssertionError("real DB")))
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _headers() -> dict[str, str]:
    token = TokenIssuer.from_env().issue_access(
        user_id=USER_ID, tenant_id=USER_ID, session_id=SESSION_ID, auth_level="PASSWORD"
    )
    return {"Authorization": f"Bearer {token}"}


def _selector_never_called(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    selector = MagicMock(side_effect=AssertionError("selector reached with invalid input"))
    monkeypatch.setattr(risk_coach, "size_for", selector)
    return selector


async def test_unknown_method_rejected_400(client, monkeypatch):
    selector = _selector_never_called(monkeypatch)
    body = _body()
    body["config"]["method"] = "MARTINGALE"
    response = await client.post(PATH, json=body, headers=_headers())
    assert response.status_code == 400
    selector.assert_not_called()


async def test_missing_state_input_rejected_400(client, monkeypatch):
    selector = _selector_never_called(monkeypatch)
    response = await client.post(PATH, json={"config": _config()}, headers=_headers())
    assert response.status_code == 400
    selector.assert_not_called()


async def test_non_numeric_price_rejected_400(client, monkeypatch):
    selector = _selector_never_called(monkeypatch)
    response = await client.post(PATH, json=_body(current_price="not-a-number"), headers=_headers())
    assert response.status_code == 400
    selector.assert_not_called()


async def test_malformed_json_body_rejected_400(client, monkeypatch):
    selector = _selector_never_called(monkeypatch)
    response = await client.post(
        PATH,
        content=b"{not json",
        headers={**_headers(), "Content-Type": "application/json"},
    )
    assert response.status_code == 400
    selector.assert_not_called()


async def test_revoked_session_rejected_401(client, monkeypatch):
    selector = _selector_never_called(monkeypatch)
    monkeypatch.setattr(deps.session_repository, "get_active", AsyncMock(return_value=None))
    response = await client.post(PATH, json=_body(), headers=_headers())
    assert response.status_code == 401
    selector.assert_not_called()


async def test_failure_injection_selector_timeout_is_closed(client, monkeypatch):
    def boom(*args):
        raise TimeoutError("selector-secret-detail")

    monkeypatch.setattr(risk_coach, "size_for", boom)
    response = await client.post(PATH, json=_body(), headers=_headers())
    assert response.status_code == 500
    assert response.json()["error_code"] == "INTERNAL_ERROR"
    assert "selector-secret-detail" not in response.text


async def test_failure_injection_session_lookup_error_denies(client, monkeypatch):
    selector = _selector_never_called(monkeypatch)
    monkeypatch.setattr(
        deps.session_repository, "get_active", AsyncMock(side_effect=ConnectionError("db down"))
    )
    response = await client.post(PATH, json=_body(), headers=_headers())
    assert response.status_code >= 400
    selector.assert_not_called()


async def test_gate_red_tampered_response_is_detected(client):
    """단언이 실제로 잡는지: 응답을 망가뜨리면 selector 대조 단언이 적색이 된다."""
    response = await client.post(PATH, json=_body(), headers=_headers())
    assert response.status_code == 200
    data = response.json()["data"]
    expected = size_for(
        PortfolioConfig.model_validate(_config()),
        PortfolioStateInput.model_validate(_state_input()),
    ).model_dump(mode="json")
    assert data == expected
    tampered = {**data, "method": "KELLY_CAPPED"}
    with pytest.raises(AssertionError):
        assert tampered == expected


@pytest.mark.perf
async def test_position_size_best_of_budget(client):
    headers = _headers()
    budget = PerfBudget()

    async def call():
        response = await client.post(PATH, json=_body(), headers=headers)
        assert response.status_code == 200

    best = await budget.best_of_async(call, n=5, warmup=1)
    assert best.cpu_ms < _BUDGET_MS, budget.describe(best, budget_ms=_BUDGET_MS)


@pytest.mark.perf
async def test_gate_red_10x_injected_delay_breaks_budget(client, monkeypatch):
    """가상 지연 10배(예산의 10배)를 selector에 주입하면 예산 단언이 적색이어야 한다."""
    perf_inject.reset_virtual_totals()
    monkeypatch.setattr(
        risk_coach,
        "size_for",
        perf_inject.virtual_delayed_sync(risk_coach.size_for, _BUDGET_MS * 10 / 1000),
    )
    headers = _headers()
    budget = PerfBudget()

    async def call():
        response = await client.post(PATH, json=_body(), headers=headers)
        assert response.status_code == 200

    best = await budget.best_of_async(call, n=3, warmup=0)
    assert best.cpu_ms >= _BUDGET_MS * 10
    with pytest.raises(AssertionError):
        assert best.cpu_ms < _BUDGET_MS
