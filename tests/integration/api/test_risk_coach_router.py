"""U-8 task-8107: 실제 인증/라우터와 인메모리 저장소로 위임을 검증한다.

실DB/거래소 및 lifespan 사용 없음. 계정 조회 403/404는 N/A:
요청 스키마에 계정/테넌트 선택자가 없고 저장 리소스를 조회하지 않는다.
위조 인증 401을 테넌트 인가 증거로 간주하지 않는다.
D3 replay_verify N/A(주문/원장 이벤트를 생성하지 않는 U-8 계산 API).
"""

from __future__ import annotations

import uuid
from time import perf_counter
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api import deps
from src.api.contracts.handlers import install_exception_handlers
from src.api.routers import risk_coach
from src.core.portfolio.config import PortfolioConfig
from src.core.portfolio.sizing.selector import SizingResultTamperedError, size_for
from src.core.portfolio.state_input import PortfolioStateInput
from src.services.auth.tokens import TokenIssuer
from src.services.auth_service import User

USER_ID = uuid.UUID(int=8107)
SESSION_ID = uuid.UUID(int=8108)
PATH = "/risk-coach/position-size"

_COST_MODEL = {"model_id": "cm-1", "cost_model_hash": "0" * 64}


def _config(method: str, **overrides: object) -> dict:
    base = {
        "method": method,
        "fraction_pct": "10",
        "rebalance_band_pct": "5",
        "min_trade_notional": "1",
        "cost_model": _COST_MODEL,
    }
    base.update(overrides)
    return base


def _state_input(config: dict, **overrides: object) -> dict:
    base = {
        "allocated_capital": "1000",
        "position_quantity": "0",
        "current_price": "100",
        "total_equity": "10000",
        "cash_available": "9000",
        "portfolio_config": config,
    }
    base.update(overrides)
    return base


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
        user_id=USER_ID, email="risk-coach@example.test", display_name=None,
        mfa_enabled=False, mfa_verified_at=None, status="ACTIVE",
        is_verifier=False, is_platform_admin=False,
    )
    monkeypatch.setattr(deps, "get_user_by_id", AsyncMock(return_value=user))
    monkeypatch.setattr(deps.session_repository, "get_active", AsyncMock(return_value=object()))
    # Any accidental real database connection is a test failure.
    monkeypatch.setattr("asyncpg.connect", MagicMock(side_effect=AssertionError("real DB")))
    monkeypatch.setattr("asyncpg.create_pool", MagicMock(side_effect=AssertionError("real DB")))
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FF_U8_RISK_COACH", "1")


async def _auth(client: AsyncClient) -> dict[str, str]:
    token = TokenIssuer.from_env().issue_access(
        user_id=USER_ID, tenant_id=USER_ID, session_id=SESSION_ID, auth_level="PASSWORD"
    )
    return {"Authorization": f"Bearer {token}"}


def test_route_is_mounted_on_app() -> None:
    from src.main import app

    paths = app.openapi()["paths"]
    assert PATH in paths
    assert "post" in paths[PATH]


def test_router_has_zero_raw_http_exception() -> None:
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[3] / "src/api/routers/risk_coach.py").read_text(
        "utf-8"
    )
    calls = [
        n
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "HTTPException"
    ]
    assert calls == []


async def test_requires_auth(client: AsyncClient) -> None:
    config = _config("FIXED_FRACTIONAL")
    response = await client.post(PATH, json={"config": config, "state_input": _state_input(config)})
    assert response.status_code == 401


async def test_forged_token_rejected(client: AsyncClient) -> None:
    config = _config("FIXED_FRACTIONAL")
    response = await client.post(
        PATH,
        json={"config": config, "state_input": _state_input(config)},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


async def test_flag_off_returns_404(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FF_U8_RISK_COACH", raising=False)
    headers = await _auth(client)
    config = _config("FIXED_FRACTIONAL")
    response = await client.post(
        PATH,
        json={"config": config, "state_input": _state_input(config)},
        headers=headers,
    )
    assert response.status_code == 404
    assert response.json()["error_code"] == "RESOURCE_NOT_FOUND"


async def test_method_mismatch_propagates_as_non_raw_http_exception(
    client: AsyncClient,
) -> None:
    headers = await _auth(client)
    config = _config("FIXED_FRACTIONAL")
    mismatched_state_input = _state_input(_config("VOLATILITY_TARGET"))
    response = await client.post(
        PATH,
        json={"config": config, "state_input": mismatched_state_input},
        headers=headers,
    )
    assert response.status_code == 500
    body = response.json()
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "trace_id" in body
    assert "Traceback" not in body["message"]


async def test_fixed_fractional_matches_selector(client: AsyncClient) -> None:
    config = _config("FIXED_FRACTIONAL")
    state_input = _state_input(config)
    headers = await _auth(client)
    response = await client.post(
        PATH, json={"config": config, "state_input": state_input}, headers=headers
    )
    assert response.status_code == 200, response.text

    expected = size_for(
        PortfolioConfig.model_validate(config), PortfolioStateInput.model_validate(state_input)
    )
    data = response.json()["data"]
    assert data["method"] == "FIXED_FRACTIONAL"
    assert data == expected.model_dump(mode="json")


async def test_volatility_target_matches_selector(client: AsyncClient) -> None:
    config = _config("VOLATILITY_TARGET", target_vol_pct="20")
    state_input = _state_input(config, realized_vol_pct="10")
    headers = await _auth(client)
    response = await client.post(
        PATH, json={"config": config, "state_input": state_input}, headers=headers
    )
    assert response.status_code == 200, response.text

    expected = size_for(
        PortfolioConfig.model_validate(config), PortfolioStateInput.model_validate(state_input)
    )
    data = response.json()["data"]
    assert data["method"] == "VOLATILITY_TARGET"
    assert data == expected.model_dump(mode="json")


async def test_kelly_capped_matches_selector(client: AsyncClient) -> None:
    config = _config("KELLY_CAPPED", kelly_cap_pct="25")
    state_input = _state_input(config, win_rate="0.6", avg_win_loss_ratio="2")
    headers = await _auth(client)
    response = await client.post(
        PATH, json={"config": config, "state_input": state_input}, headers=headers
    )
    assert response.status_code == 200, response.text

    expected = size_for(
        PortfolioConfig.model_validate(config), PortfolioStateInput.model_validate(state_input)
    )
    data = response.json()["data"]
    assert data["method"] == "KELLY_CAPPED"
    assert data == expected.model_dump(mode="json")


async def test_risk_parity_matches_selector(client: AsyncClient) -> None:
    config = _config("RISK_PARITY")
    state_input = _state_input(
        config,
        realized_vol_pct="10",
        exposures={
            "total_equity": "10000",
            "per_symbol_pct": {},
            "per_strategy_pct": {},
            "total_exposure_pct": "20",
            "cash_pct": "80",
            "as_of": "2026-09-27T00:00:00+00:00",
        },
    )
    headers = await _auth(client)
    response = await client.post(
        PATH, json={"config": config, "state_input": state_input}, headers=headers
    )
    assert response.status_code == 200, response.text

    expected = size_for(
        PortfolioConfig.model_validate(config), PortfolioStateInput.model_validate(state_input)
    )
    data = response.json()["data"]
    assert data["method"] == "RISK_PARITY"
    assert data == expected.model_dump(mode="json")


async def test_domain_exception_propagates_unchanged():
    config = _config("FIXED_FRACTIONAL")
    body = risk_coach.PositionSizeRequest(
        config=PortfolioConfig.model_validate(config),
        state_input=PortfolioStateInput.model_validate(_state_input(_config("RISK_PARITY"))),
    )
    with pytest.raises(SizingResultTamperedError):
        await risk_coach.post_position_size(body, _user=None, _flag=None)


async def test_selector_failure_is_closed(client, monkeypatch):
    def fail(*args):
        raise RuntimeError("private-selector-detail")

    monkeypatch.setattr(risk_coach, "size_for", fail)
    config = _config("FIXED_FRACTIONAL")
    response = await client.post(
        PATH, json={"config": config, "state_input": _state_input(config)},
        headers=await _auth(client),
    )
    assert response.status_code == 500
    assert response.json()["error_code"] == "INTERNAL_ERROR"
    assert "private-selector-detail" not in response.text


async def test_disabled_flag_never_calls_selector(client, monkeypatch):
    monkeypatch.delenv("FF_U8_RISK_COACH", raising=False)
    selector = MagicMock(side_effect=AssertionError("selector called while disabled"))
    monkeypatch.setattr(risk_coach, "size_for", selector)
    config = _config("FIXED_FRACTIONAL")
    response = await client.post(
        PATH, json={"config": config, "state_input": _state_input(config)},
        headers=await _auth(client),
    )
    assert response.status_code == 404
    selector.assert_not_called()


async def test_position_size_p95_budget(client):
    # U-8 has no dedicated ADR budget; borrow the 200 ms read-query budget.
    # This measures the in-memory API, not production network latency.
    config = _config("FIXED_FRACTIONAL")
    headers = await _auth(client)
    samples = []
    for _ in range(30):
        start = perf_counter()
        response = await client.post(
            PATH, json={"config": config, "state_input": _state_input(config)}, headers=headers,
        )
        samples.append((perf_counter() - start) * 1000)
        assert response.status_code == 200
    assert sorted(samples)[28] < 200
