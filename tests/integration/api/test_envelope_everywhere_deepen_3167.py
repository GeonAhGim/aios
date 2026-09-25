"""통합테스트 — task-3167 DEEPEN: PLT-17~21(task-1016) 봉투/예외매핑 실질 증빙.

원 리프(task-1016, `tests/integration/api/test_envelope_everywhere.py` +
`tests/integration/api/test_no_raw_http_exception.py`)는 executions/
portfolio/reports 라우터에 대해 negative 테스트(실서비스가 실제로 던지는
도메인 예외 1건씩)와 게이트 적색 재현(AST 탐지기 자체를 대상으로 한
negative test)은 있었지만, 실패 주입(의존성을 fake로 바꿔치기해 그
fake가 항상 성공만 반환하지 않고 실제로 실패를 뚫고 나가는지 보는 것)과
수치 성능 단언이 0건이라 D2 하한(ADR-2026-09-09-C Decision 1) 미달이었다
(task-3160/3164/3165 DEEPEN과 동일 결함 패턴, 대상 라우터만 다름).

이 파일은 원 파일들을 건드리지 않고(500줄 loc 래칫 회귀 방지,
ADR-2026-09-10-C §7) `GET /reports`(reports.py `generate_report`,
`ReportService` 의존성)와 `POST /portfolio/rebalance`(portfolio.py
`rebalance`, `PortfolioService` 의존성)를 대상으로 task-3165 DEEPEN과
동일한 세 가지를 새로 고정한다:

- 실패 주입 1: EXCEPTION_MAP에 없는 미분류 예외가 서비스 계층에서 터지면
  전역 핸들러(`handlers.py` `_handle_domain_or_unknown_exception`)가 이를
  삼켜 위장 성공을 반환하지 않으면서도, 원본 예외 메시지(민감정보일 수
  있음)는 누출하지 않고 고정된 일반 메시지 + INTERNAL_ERROR로
  봉투화하는지(fail-closed) 확인한다.
- 실패 주입 2: `_STATUS_DEFAULT_CODE`에 없는 상태코드로 레거시
  `HTTPException`이 발생해도(이관 안 된 의존성 대비 fallback 경로) 봉투
  모양이 깨지지 않고, 라우터가 명시적으로 고른 상태코드가 500으로
  바꿔치기되지 않는지 확인한다.
- 수치 성능 단언: ADR-2026-09-09-C Decision 1 예산표에 봉투 조회 전용
  항목이 없어 가장 가까운 유사 항목("주문 제출→ACK p95 50ms(paper)", 동일
  하게 단일 실DB 왕복)을 자체 예산으로 차용한다(task-3148/3156/3160/3164/
  3165 DEEPEN과 동일 차용 근거) — `ExecutionMonitoringService.list_for_user()`
  (strategy_executions/positions 단일 JOIN 조회, 실행 없는 신규 사용자
  기준 단일 왕복) p95를 그 예산 내로 단언하고, 지연 주입으로 그 단언이
  실제로 깨짐을 재현한다.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-17~21
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid

import asyncpg
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from src.api.contracts.envelope import ApiResponse, ok
from src.api.contracts.handlers import install_exception_handlers
from src.api.portfolio_deps import get_portfolio_service
from src.api.reports_deps import get_report_service
from src.main import app
from src.services.execution_monitoring_service import ExecutionMonitoringService
from tests.integration.conftest import create_test_user

STRONG_PASSWORD = "Str0ng!Passw0rd"


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    app.dependency_overrides.clear()


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


async def _register_user(client: AsyncClient) -> dict:
    email = _unique_email()
    response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    body = response.json()
    return {"Authorization": f"Bearer {body['data']['access_token']}"}


# --- 실패 주입 1: 미분류 예외가 원본 메시지를 누출하지 않고 fail-closed로 봉투화 ---


class _UnclassifiedDependencyFailure(Exception):
    pass


class _ExplodingReportService:
    async def generate_report(self, user_id: object, *args: object, **kwargs: object) -> object:
        raise _UnclassifiedDependencyFailure(
            "connection string: postgresql://admin:hunter2@internal-db/prod"
        )


async def test_unclassified_exception_is_enveloped_without_leaking_original_message(client):
    """실패 주입: EXCEPTION_MAP에 없는 예외가 서비스 계층에서 터지면
    (`get_report_service` 의존성 오버라이드로 시뮬레이션), 전역 핸들러가
    이를 삼켜 위장 성공을 반환하지 않으면서도, 원본 예외 메시지(연결
    문자열 등 민감정보일 수 있음)는 응답에 그대로 노출하지 않고 고정
    일반 메시지 + INTERNAL_ERROR로 봉투화하는지 확인한다."""
    headers = await _register_user(client)
    app.dependency_overrides[get_report_service] = lambda: _ExplodingReportService()
    try:
        response = await client.get(
            "/reports",
            params={"period_start": "2026-01-01", "period_end": "2026-01-31"},
            headers=headers,
        )
    finally:
        del app.dependency_overrides[get_report_service]

    assert response.status_code == 500
    body = response.json()
    assert set(body.keys()) >= {"error_code", "message", "trace_id"}
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "hunter2" not in body["message"]
    assert body["details"] == {}


# --- 실패 주입 2: 매핑 안 된 상태코드의 레거시 HTTPException도 봉투·상태코드 보존 ---


class _RawHttpExceptionPortfolioService:
    async def rebalance(self, user_id: object, adjustments: object, **kwargs: object) -> object:
        raise HTTPException(status_code=402, detail="Payment required")


async def test_raw_http_exception_with_unmapped_status_code_keeps_envelope_shape_and_status(
    client,
):
    """실패 주입: `_STATUS_DEFAULT_CODE`(handlers.py)에 없는 상태코드(402)로
    레거시 `HTTPException`이 발생해도(이관 안 된 의존성 대비 fallback 경로)
    응답이 §15.3 ApiError 봉투 모양을 유지하고, 라우터가 명시적으로 고른
    402 상태코드가 500으로 바뀌지 않는지 확인한다. 사용자가 연동한 거래소가
    없어 `_total_cash_balance`가 resolver를 거치지 않고 0을 반환하므로,
    `get_portfolio_service` 하나만 오버라이드하면 된다."""
    headers = await _register_user(client)
    app.dependency_overrides[get_portfolio_service] = lambda: _RawHttpExceptionPortfolioService()
    try:
        response = await client.post(
            "/portfolio/rebalance",
            json={"adjustments": [{"execution_id": 1, "new_allocated_capital": "500"}]},
            headers=headers,
        )
    finally:
        del app.dependency_overrides[get_portfolio_service]

    assert response.status_code == 402
    body = response.json()
    assert set(body.keys()) >= {"error_code", "message", "trace_id"}
    assert body["error_code"] == "INTERNAL_ERROR"
    assert body["message"] == "Payment required"


# --- DB 없이 핸들러 계약 자체를 고정하는 보강 테스트(이 리프의 hardening 대상) ---
#
# 위 두 실패 주입은 `client`/`pool` 픽스처(app.router.lifespan_context →
# asyncpg.create_pool, PLT-17~21 라우터 전체 스택)를 경유해야만 돌아 실DB가
# 필요하다. 여기서는 `install_exception_handlers`(handlers.py)만 붙인 최소
# FastAPI 앱으로 핸들러 자체의 계약 — (a) 미분류 예외가 원본 메시지/타입/
# 트레이스백을 누출하지 않고 봉투화되는지, (b) 성공·실패 양쪽 응답이
# §15.3/§2.3 봉투 모양을 실제로 갖추는지(봉투 누락을 탐지할 수 있는지),
# (c) 검증 오류와 레거시 HTTPException이 올바른 코드로 봉투화되는지 —
# 를 실DB 없이 고정한다(task-3167 DEEPEN 원 파일들과 동일하게 이 파일도
# 건드리지 않는다).


class _UnclassifiedHandlerFailure(RuntimeError):
    """EXCEPTION_MAP 어디에도 없는, 서비스 계층에서 올라올 법한 미분류 예외."""


class _ValidatedBody(BaseModel):
    count: int


def _build_bare_envelope_app() -> FastAPI:
    bare_app = FastAPI()
    install_exception_handlers(bare_app)

    @bare_app.get("/ok")
    async def _ok_endpoint() -> ApiResponse[dict[str, int]]:
        return ok({"value": 1})

    @bare_app.get("/boom")
    async def _boom_endpoint() -> None:
        raise _UnclassifiedHandlerFailure(
            "connection string: postgresql://admin:hunter2@internal-db/prod"
        )

    @bare_app.get("/legacy-not-found")
    async def _legacy_not_found_endpoint() -> None:
        raise HTTPException(status_code=404, detail="widget xyz not found")

    @bare_app.post("/validate")
    async def _validate_endpoint(body: _ValidatedBody) -> None:
        return None

    return bare_app


@pytest.fixture
async def bare_client():
    bare_app = _build_bare_envelope_app()
    transport = ASGITransport(app=bare_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_unclassified_exception_envelope_leaks_no_message_type_or_traceback(
    bare_client,
):
    """(a) 미분류 예외는 표준 오류 봉투로 반환되고, 응답 본문(raw text
    포함)에는 원본 예외 메시지도, 예외 타입 이름도, 트레이스백도 남지
    않는다 — fail-closed 고정 메시지 + INTERNAL_ERROR만 노출한다."""
    response = await bare_client.get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert set(body.keys()) >= {"error_code", "message", "trace_id"}
    assert body["error_code"] == "INTERNAL_ERROR"
    assert body["details"] == {}

    raw_text = response.text
    for leaked in (
        "hunter2",
        "postgresql://",
        "_UnclassifiedHandlerFailure",
        "Traceback",
        "internal-db",
    ):
        assert leaked not in body["message"]
        assert leaked not in raw_text


async def test_envelope_keys_present_on_both_success_and_error_paths(bare_client):
    """(b) 성공 응답은 §2.3 `ApiResponse`(data/meta.trace_id/meta.as_of)
    모양을, 실패 응답은 §15.3 `ApiError`(error_code/message/details/
    trace_id/retry_after_seconds) 모양을 실제로 갖춘다 — 어느 한쪽이라도
    봉투 필드가 빠지면(예: data 없이 raw dict만 반환) 이 단언이 잡는다."""
    ok_response = await bare_client.get("/ok")
    assert ok_response.status_code == 200
    ok_body = ok_response.json()
    assert set(ok_body.keys()) == {"data", "meta"}
    assert ok_body["data"] == {"value": 1}
    assert set(ok_body["meta"].keys()) >= {"trace_id", "as_of"}

    error_response = await bare_client.get("/boom")
    assert error_response.status_code == 500
    error_body = error_response.json()
    assert set(error_body.keys()) == {
        "error_code",
        "message",
        "details",
        "trace_id",
        "retry_after_seconds",
    }


async def test_validation_error_is_enveloped_with_validation_invalid_field_code(bare_client):
    """(c-1) pydantic 바디 검증 실패(`RequestValidationError`)는 raw
    FastAPI 422 기본 형식이 아니라 §3.3 VALIDATION_INVALID_FIELD + 400으로
    봉투화된다."""
    response = await bare_client.post("/validate", json={"count": "not-an-int"})

    assert response.status_code == 400
    body = response.json()
    assert set(body.keys()) >= {"error_code", "message", "trace_id"}
    assert body["error_code"] == "VALIDATION_INVALID_FIELD"
    assert body["details"]["fields"] == ["body.count"]


async def test_legacy_http_exception_with_mapped_status_uses_matching_error_code(bare_client):
    """(c-2) `_STATUS_DEFAULT_CODE`에 있는 상태코드(404)로 레거시
    `HTTPException`이 발생하면 대응하는 RESOURCE_NOT_FOUND로 봉투화되고,
    라우터가 고른 상태코드(404)와 원본 detail 메시지가 그대로 보존된다."""
    response = await bare_client.get("/legacy-not-found")

    assert response.status_code == 404
    body = response.json()
    assert set(body.keys()) >= {"error_code", "message", "trace_id"}
    assert body["error_code"] == "RESOURCE_NOT_FOUND"
    assert body["message"] == "widget xyz not found"


# --- 수치 성능 단언: ExecutionMonitoringService.list_for_user() 단일 JOIN 조회 p95 ---

_PERF_ITERATIONS = 30
_PERF_BUDGET_MS = 50.0  # ADR-2026-09-09-C Decision 1의 "주문 제출→ACK p95
# 50ms(paper)"를 가장 가까운 유사 항목으로 차용(봉투 조회 전용 예산 항목
# 없음) — 둘 다 단일 실DB 왕복(실행 없는 사용자 기준 JOIN 조회 1회)이라는
# 점에서 비교 가능한 부하 특성을 가진다(task-3148 PLT-22, task-3156 PLT-07,
# task-3160/3164/3165 PLT-17~21 DEEPEN과 동일 차용 근거).


async def _list_for_user_p95_ms(pool: asyncpg.Pool, user_id: uuid.UUID, *, n: int) -> float:
    service = ExecutionMonitoringService(pool)
    durations_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        await service.list_for_user(user_id)
        durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    return durations_ms[int(len(durations_ms) * 0.95)]


async def test_list_for_user_p95_under_borrowed_single_roundtrip_budget(pool):
    user_id = await create_test_user(pool)

    p95_ms = await _list_for_user_p95_ms(pool, user_id, n=_PERF_ITERATIONS)

    assert p95_ms < _PERF_BUDGET_MS


async def test_list_for_user_budget_gate_fails_on_injected_regression(pool, monkeypatch):
    """게이트 적색 재현: 위 p95 단언이 실제로 회귀를 잡는지 확인한다 —
    `asyncpg.Connection.fetch`에 60ms 인위 지연을 주입해, 같은 측정
    로직이 실제로 AssertionError를 내는지 본다(tautology가 아님을 증명)."""
    user_id = await create_test_user(pool)
    original_fetch = asyncpg.Connection.fetch

    async def _slow_fetch(self: asyncpg.Connection, *args: object, **kwargs: object) -> object:
        await asyncio.sleep(0.06)
        return await original_fetch(self, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetch", _slow_fetch)

    p95_ms = await _list_for_user_p95_ms(pool, user_id, n=5)

    with pytest.raises(AssertionError):
        assert p95_ms < _PERF_BUDGET_MS
