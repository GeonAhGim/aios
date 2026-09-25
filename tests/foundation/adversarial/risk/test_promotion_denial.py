"""U-15 DoD "승격 거부 테스트" — PAPER→LIVE 체크리스트 미충족 시 fail-closed
거부를 애플리케이션 계층과 API 계층 양쪽에서 검증한다.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.contracts.handlers import install_exception_handlers
from src.api.deps import get_current_admin
from src.api.routers import personal
from src.foundation.risk.adapters.json_state_store import JsonPersonalStateStore
from src.foundation.risk.application.promotion_checklist import (
    PromotionDeniedError,
    request_promotion,
)
from src.services.auth_service import User

_CONDITION2_ENV = "PERSONAL_ADR_0829E_CONDITION2_MET"


def _admin_user() -> User:
    return User(
        user_id=uuid.uuid4(),
        email="personal-mode-admin@example.com",
        display_name=None,
        mfa_enabled=False,
        mfa_verified_at=None,
        status="ACTIVE",
        is_verifier=False,
        is_platform_admin=True,
    )


@pytest.fixture(autouse=True)
def _clear_condition2_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(_CONDITION2_ENV, raising=False)


async def test_request_promotion_denied_when_condition2_unset(tmp_path: Path):
    state = JsonPersonalStateStore(path=tmp_path / "state.json")

    with pytest.raises(PromotionDeniedError) as exc_info:
        await request_promotion(bundle_active=True, state=state)

    assert "ADR_0829E_CONDITION2_UNMET" in exc_info.value.blockers


async def test_request_promotion_denied_when_paper_history_too_short(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(_CONDITION2_ENV, "true")
    state = JsonPersonalStateStore(path=tmp_path / "state.json")

    with pytest.raises(PromotionDeniedError) as exc_info:
        await request_promotion(bundle_active=True, state=state)

    assert "INSUFFICIENT_PAPER_HISTORY" in exc_info.value.blockers


async def test_request_promotion_denied_when_violations_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(_CONDITION2_ENV, "true")
    state = JsonPersonalStateStore(path=tmp_path / "state.json")
    eight_days_ago = date.today() - timedelta(days=8)
    await state.mark_paper_started_if_unset(today=eight_days_ago)
    await state.record_violation(occurred_on=eight_days_ago + timedelta(days=1))

    with pytest.raises(PromotionDeniedError) as exc_info:
        await request_promotion(bundle_active=True, state=state)

    assert "PAPER_VIOLATIONS_PRESENT" in exc_info.value.blockers


async def test_request_promotion_succeeds_when_all_conditions_met(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(_CONDITION2_ENV, "true")
    state = JsonPersonalStateStore(path=tmp_path / "state.json")
    eight_days_ago = date.today() - timedelta(days=8)
    await state.mark_paper_started_if_unset(today=eight_days_ago)

    checklist = await request_promotion(bundle_active=True, state=state)

    assert checklist.eligible is True
    assert checklist.blockers == ()


def _make_app(state_path: Path) -> FastAPI:
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(personal.router)
    app.dependency_overrides[get_current_admin] = _admin_user
    app.dependency_overrides[personal.get_personal_state] = lambda: JsonPersonalStateStore(
        path=state_path
    )
    return app


async def test_api_promote_denied_returns_403_policy_live_blocked(tmp_path: Path):
    app = _make_app(tmp_path / "state.json")
    # Starlette's ServerErrorMiddleware re-raises after sending the response
    # (by design, for server-side logging) — raise_app_exceptions=False tells
    # httpx to return that already-sent response instead of re-raising too
    # (same pattern as tests/integration/api/test_idempotency_digest.py).
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/foundation/personal/promote")

    assert resp.status_code == 403
    body = resp.json()
    assert body["error_code"] == "POLICY_LIVE_BLOCKED"
    assert "ADR_0829E_CONDITION2_UNMET" in body["details"]["blockers"]
    assert "INSUFFICIENT_PAPER_HISTORY" in body["details"]["blockers"]


async def test_api_promotion_checklist_reflects_denial_without_raising(tmp_path: Path):
    app = _make_app(tmp_path / "state.json")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/foundation/personal/promotion-checklist")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["eligible"] is False
    assert data["min_paper_days"] == 7
