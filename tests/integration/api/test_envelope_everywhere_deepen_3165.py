"""통합테스트 — task-3165 DEEPEN: PLT-17~21(task-1017) 봉투/예외매핑 실질 증빙.

원 리프(task-1017, `tests/integration/api/test_envelope_everywhere.py` +
`tests/integration/api/test_no_raw_http_exception.py`)는 notifications/
alerts/device_tokens/wallet 라우터에 대해 negative 테스트와 게이트 적색
재현(AST 탐지기 자체를 대상으로 한 negative test)은 있었지만, 실패 주입과
수치 성능 단언이 0건이라 D2 하한(ADR-2026-09-09-C Decision 1) 미달이었다
(task-3160/task-3164 DEEPEN과 동일 결함 패턴, 대상 라우터만 다름).

이 파일은 원 파일들을 건드리지 않고(500줄 loc 래칫 회귀 방지,
ADR-2026-09-10-C §7) `POST /wallet/topup-requests`(wallet.py
`request_topup`, `WalletService.request_topup` 단일 INSERT 왕복)와
`POST /alerts`(alerts.py `create_alert`, `AlertService` 의존성)를 대상으로
task-3160/task-3164 DEEPEN과 동일한 세 가지를 새로 고정한다:

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
  하게 단일 실DB 왕복 — wallet.py 모듈 docstring이 밝히듯 이 라우터는
  실제 금전 라우트라 이 유사 항목이 다른 DEEPEN보다 한층 더 근접하다)을
  자체 예산으로 차용한다(task-3148/3156/3160/3164 DEEPEN과 동일 차용
  근거) — `WalletService.request_topup()` 단일 INSERT 왕복 p95를 그 예산
  내로 단언하고, 지연 주입으로 그 단언이 실제로 깨짐을 재현한다.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-17~21
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from decimal import Decimal

import asyncpg
import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from src.api.service_deps import get_alert_service, get_wallet_service
from src.main import app
from src.services.wallet_service import WalletService
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


class _ExplodingWalletService:
    async def request_topup(self, user_id: object, amount: object) -> object:
        raise _UnclassifiedDependencyFailure(
            "connection string: postgresql://admin:hunter2@internal-db/prod"
        )


async def test_unclassified_exception_is_enveloped_without_leaking_original_message(client):
    """실패 주입: EXCEPTION_MAP에 없는 예외가 서비스 계층에서 터지면
    (`get_wallet_service` 의존성 오버라이드로 시뮬레이션), 전역 핸들러가
    이를 삼켜 위장 성공을 반환하지 않으면서도, 원본 예외 메시지(연결
    문자열 등 민감정보일 수 있음)는 응답에 그대로 노출하지 않고 고정
    일반 메시지 + INTERNAL_ERROR로 봉투화하는지 확인한다."""
    headers = await _register_user(client)
    app.dependency_overrides[get_wallet_service] = lambda: _ExplodingWalletService()
    try:
        response = await client.post(
            "/wallet/topup-requests", json={"amount": "100"}, headers=headers
        )
    finally:
        del app.dependency_overrides[get_wallet_service]

    assert response.status_code == 500
    body = response.json()
    assert set(body.keys()) >= {"error_code", "message", "trace_id"}
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "hunter2" not in body["message"]
    assert body["details"] == {}


# --- 실패 주입 2: 매핑 안 된 상태코드의 레거시 HTTPException도 봉투·상태코드 보존 ---


class _RawHttpExceptionAlertService:
    async def create_alert(self, user_id: object, **kwargs: object) -> object:
        raise HTTPException(status_code=402, detail="Payment required")


async def test_raw_http_exception_with_unmapped_status_code_keeps_envelope_shape_and_status(
    client,
):
    """실패 주입: `_STATUS_DEFAULT_CODE`(handlers.py)에 없는 상태코드(402)로
    레거시 `HTTPException`이 발생해도(이관 안 된 의존성 대비 fallback 경로)
    응답이 §15.3 ApiError 봉투 모양을 유지하고, 라우터가 명시적으로 고른
    402 상태코드가 500으로 바뀌지 않는지 확인한다."""
    headers = await _register_user(client)
    app.dependency_overrides[get_alert_service] = lambda: _RawHttpExceptionAlertService()
    try:
        response = await client.post(
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
    finally:
        del app.dependency_overrides[get_alert_service]

    assert response.status_code == 402
    body = response.json()
    assert set(body.keys()) >= {"error_code", "message", "trace_id"}
    assert body["error_code"] == "INTERNAL_ERROR"
    assert body["message"] == "Payment required"


# --- 수치 성능 단언: WalletService.request_topup() 단일 INSERT 왕복 p95 ---

_PERF_ITERATIONS = 30
_PERF_BUDGET_MS = 50.0  # ADR-2026-09-09-C Decision 1의 "주문 제출→ACK p95
# 50ms(paper)"를 가장 가까운 유사 항목으로 차용(봉투 조회 전용 예산 항목
# 없음) — 둘 다 단일 실DB 왕복(INSERT 1회)이라는 점에서 비교 가능한 부하
# 특성을 가진다(task-3148 PLT-22, task-3156 PLT-07, task-3160/3164
# PLT-17~21 DEEPEN과 동일 차용 근거).


async def _request_topup_p95_ms(pool: asyncpg.Pool, user_id: uuid.UUID, *, n: int) -> float:
    service = WalletService(pool)
    durations_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        await service.request_topup(user_id, Decimal("100"))
        durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    return durations_ms[int(len(durations_ms) * 0.95)]


@pytest.mark.perf
async def test_request_topup_p95_under_borrowed_single_roundtrip_budget(pool):
    """Wall-clock p95 over real DB round trips -- runs in the serial perf
    stage (task-7434): under `-n auto` core contention the same call measured
    103-117ms against a 50ms budget on two unrelated dependency PRs
    (runs 36228616048 / 36228613530) while passing in isolation. The timer
    lives in `_request_topup_p95_ms`, so the AST perf-marker guard (which only
    inspects the test body) did not flag it."""
    user_id = await create_test_user(pool)

    p95_ms = await _request_topup_p95_ms(pool, user_id, n=_PERF_ITERATIONS)

    assert p95_ms < _PERF_BUDGET_MS


async def test_request_topup_budget_gate_fails_on_injected_regression(pool, monkeypatch):
    """게이트 적색 재현: 위 p95 단언이 실제로 회귀를 잡는지 확인한다 —
    `asyncpg.Connection.fetchrow`에 60ms 인위 지연을 주입해, 같은 측정
    로직이 실제로 AssertionError를 내는지 본다(tautology가 아님을 증명)."""
    user_id = await create_test_user(pool)
    original_fetchrow = asyncpg.Connection.fetchrow

    async def _slow_fetchrow(self: asyncpg.Connection, *args: object, **kwargs: object) -> object:
        await asyncio.sleep(0.06)
        return await original_fetchrow(self, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", _slow_fetchrow)

    p95_ms = await _request_topup_p95_ms(pool, user_id, n=5)

    with pytest.raises(AssertionError):
        assert p95_ms < _PERF_BUDGET_MS
