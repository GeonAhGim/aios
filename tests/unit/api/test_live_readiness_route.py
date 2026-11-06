"""LP-2 라우트: GET /executions/{id}/live-readiness -- 인증·거부 매핑·응답 모양."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.contracts.handlers import install_exception_handlers
from src.api.deps import get_current_user
from src.api.execution_deps import get_live_readiness_service
from src.api.routers import executions
from src.services.auth_service import User
from src.services.execution_types import ExecutionControlError
from src.services.live_readiness import compute_live_readiness

NOW = datetime(2026, 11, 7, tzinfo=UTC)


class _FakeService:
    def __init__(self, outcome):
        self._outcome = outcome
        self.calls: list[tuple[int, object]] = []

    async def for_execution(self, execution_id, user_id):
        self.calls.append((execution_id, user_id))
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _user() -> User:
    return User(
        user_id=uuid4(),
        email="a@b.c",
        display_name=None,
        mfa_enabled=False,
        mfa_verified_at=None,
        status="ACTIVE",
        is_verifier=False,
        is_platform_admin=False,
    )


def _client(service, user: User | None):
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(executions.router, prefix="/executions")
    app.dependency_overrides[get_live_readiness_service] = lambda: service
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def _rows():
    return [
        {
            "status": "FILLED",
            "side": "BUY",
            "quantity": "10",
            "filled_quantity": "10",
            "price": "100",
            "average_fill_price": "101",
            "created_at": NOW,
            "updated_at": NOW,
        }
    ]


def test_returns_readiness_with_unavailable_reasons_as_null_not_zero() -> None:
    service = _FakeService(compute_live_readiness(_rows()))
    user = _user()
    res = _client(service, user).get("/executions/7/live-readiness")
    assert res.status_code == 200
    body = res.json()
    assert body["order_count"] == 1 and body["filled_count"] == 1
    assert Decimal(str(body["avg_slippage_bps"])) == Decimal(
        "100"
    )  # 매수 100 -> 101 = +100bps 불리
    assert body["net_pnl"] is None and "net_pnl" in body["unavailable"]
    assert body["fee_totals"] is None and "fee_totals" in body["unavailable"]
    assert service.calls == [(7, user.user_id)]  # 로그인한 사용자 id가 서비스로 넘어간다


@pytest.mark.parametrize(
    "message",
    [
        "존재하지 않는 실행입니다.",
        "본인의 실행만 조회할 수 있습니다.",
        "PAPER 모드 실행만 전환 근거를 조회할 수 있습니다.",
    ],
)
def test_service_rejections_are_mapped_by_the_global_handler(message) -> None:
    res = _client(_FakeService(ExecutionControlError(message)), _user()).get(
        "/executions/7/live-readiness"
    )
    assert res.status_code in (400, 403, 404)
    assert res.status_code != 500


def test_route_depends_on_the_authenticated_user() -> None:
    """인증은 get_current_user 의존성으로 건다 -- 라우트가 그 의존성을 선언했는지 확인한다."""
    route = next(
        r
        for r in executions.router.routes
        if getattr(r, "path", "") == "/{execution_id}/live-readiness"
    )
    assert route.methods == {"GET"}
    assert get_current_user in {d.call for d in route.dependant.dependencies}


def test_non_integer_execution_id_is_rejected_by_validation() -> None:
    res = _client(_FakeService(compute_live_readiness([])), _user()).get(
        "/executions/abc/live-readiness"
    )
    assert (
        res.status_code == 400
    )  # 이 프로젝트는 검증 오류를 VALIDATION_INVALID_FIELD(400)로 매핑한다
