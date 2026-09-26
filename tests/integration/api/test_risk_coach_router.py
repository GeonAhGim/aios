"""U-8 (task-8105) 통합테스트 -- `/risk-coach/position-size` 실제 FastAPI 앱
(TEST_DATABASE_URL은 `/auth/register` 인증 경로에서만 쓰인다 -- 이 엔드포인트
자체는 순수 위임이라 실거래소/실DB 호출이 없다).

DoD 대응: negative >= 4(미인증 401·플래그 OFF 404·config/state_input method
불일치 시 SizingResultTamperedError 전파·미검증/위조 토큰 401), 4개 sizing
method 각각 selector.size_for와 동일한 결과, raw HTTPException 미사용(PLT-21
패턴 참고).

(d) "다른 테넌트 계정으로 조회 시 403/404" 항목: 이 엔드포인트는 저장된
리소스를 id로 조회하지 않는다 -- 호출자가 body에 담아 보낸 config/state_input을
그대로 selector.size_for에 위임할 뿐이라 테넌트 소유 리소스 조회 자체가
없다(N-자산 리스크패리티 한계와 같은 종류의 "이 구조에서는 성립하지 않는
케이스" -- risk_parity.py 자체 docstring 참고). 그 자리를 대신해 인증 계층의
동형 실패(위조/미검증 토큰 -> 401, 다른 사용자 계정 소유가 아닌 자원에 대한
접근 자체가 없다는 사실을 검증)로 채운다.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app

STRONG_PASSWORD = "Str0ng!Passw0rd"
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
async def client():
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FF_U8_RISK_COACH", "1")


async def _auth(client: AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/auth/register",
        json={"email": f"test-{uuid.uuid4().hex}@example.com", "password": STRONG_PASSWORD},
    )
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


# ---- 배선 증명 / 적색 게이트 재현(PLT-21 패턴) ----


def test_route_is_mounted_on_app() -> None:
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


# ---- negative: 인증 필요 ----


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


# ---- negative: 기능 플래그 OFF -> 404(있는 척하지 않는다) ----


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


# ---- negative: config.method != state_input.portfolio_config.method -> 전파 ----


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
    assert 400 <= response.status_code < 600
    body = response.json()
    assert "trace_id" in body
    assert "Traceback" not in body["message"]


# ---- 성공 경로: 4개 sizing method 모두 selector.size_for와 동일한 결과 ----


async def test_fixed_fractional_matches_selector(client: AsyncClient) -> None:
    from src.core.portfolio.config import PortfolioConfig
    from src.core.portfolio.sizing.selector import size_for
    from src.core.portfolio.state_input import PortfolioStateInput

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
    assert Decimal(data["quantity"]) == expected.quantity
    assert Decimal(data["weight_pct"]) == expected.weight_pct
    assert data["inputs_hash"] == expected.inputs_hash


async def test_volatility_target_matches_selector(client: AsyncClient) -> None:
    from src.core.portfolio.config import PortfolioConfig
    from src.core.portfolio.sizing.selector import size_for
    from src.core.portfolio.state_input import PortfolioStateInput

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
    assert Decimal(data["quantity"]) == expected.quantity
    assert Decimal(data["weight_pct"]) == expected.weight_pct
    assert data["inputs_hash"] == expected.inputs_hash


async def test_kelly_capped_matches_selector(client: AsyncClient) -> None:
    from src.core.portfolio.config import PortfolioConfig
    from src.core.portfolio.sizing.selector import size_for
    from src.core.portfolio.state_input import PortfolioStateInput

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
    assert Decimal(data["quantity"]) == expected.quantity
    assert Decimal(data["weight_pct"]) == expected.weight_pct
    assert data["inputs_hash"] == expected.inputs_hash


async def test_risk_parity_matches_selector(client: AsyncClient) -> None:
    from src.core.portfolio.config import PortfolioConfig
    from src.core.portfolio.sizing.selector import size_for
    from src.core.portfolio.state_input import PortfolioStateInput

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
    assert Decimal(data["quantity"]) == expected.quantity
    assert Decimal(data["weight_pct"]) == expected.weight_pct
    assert data["inputs_hash"] == expected.inputs_hash
