"""PLT-35-fix(task-3850) -- break-glass grant HTTP 배선 통합테스트.

QA(task-3794) 결함 I-10: `require_break_glass`/`get_current_mfa_admin`가
`tests/platform/unit/test_admin_mfa_gate.py`·`tests/platform/integration/
test_break_glass.py`에서 함수를 직접 호출하는 방식으로만 검증됐다 -- 어떤
실제 라우트에도 배선되지 않아 "admin 라우트 MFA 미달 403" DoD가 운영
코드에서 재현 불가능했다. 이 파일은 실제 FastAPI 앱 + 실제 dev DB +
TestClient(httpx ASGITransport)로 DI 체인 전체를 태워 그 403/409/200을
증명한다 -- 의존성 함수를 직접 부르지 않는다.

ADR-2026-09-09-C D2 증빙:
- negative >= 3: MFA 미검증 admin 403(감사로그/원장/리스크게이트 3라우트),
  grant 헤더 누락 400, 존재하지 않는 grant 409, 자기승인 403, 스코프 불일치
  409.
- 실패 주입 1: `test_consume_failure_during_http_request_rolls_back_grant_state`
  (HTTP 경로로 들어와도 `require_break_glass`의 `conn.transaction()`이 실제
  DB 오류를 롤백함을 증명 -- grant가 반쯤 소비된 상태로 남지 않는다).
- 성능 단언 1: `test_audit_log_route_round_trip_latency_budget`.
- 게이트 적색 재현 2건:
  1. `test_unfixed_admin_route_still_allows_non_mfa_admin_showing_the_original_gap`
     (이 leaf가 고치지 않은 15+개 `get_current_admin` 전용 라우트 중 하나가
     여전히 MFA 없이 통과됨을 보여, QA가 원래 발견한 결함이 이 3개 라우트
     밖에서는 아직 열려 있다는 CTO 결정의 경계를 그대로 증명한다).
  2. `test_request_grant_rejects_stale_mfa_step_up_through_http`(task-6482,
     task-3795 재검 반영) -- `auth_level` 클레임만 봤다면 통과했을 16분 전
     TOTP 세션이 실제 HTTP 경로에서 403으로 막힌다.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from uuid import uuid4

import asyncpg
import pyotp
import pytest
from dotenv import dotenv_values
from httpx import ASGITransport, AsyncClient

from src.main import app
from tests.integration.mfa_clock import mfa_clock_shifted, totp_at

STRONG_PASSWORD = "Str0ng!Passw0rd"


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
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


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


async def _make_admin(pool, user_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET is_platform_admin = true WHERE user_id = $1", uuid.UUID(user_id)
        )


async def _register_admin(client, pool) -> tuple[dict, str, str]:
    """PASSWORD 인증 수준(=MFA 미검증)인 admin 하나를 만든다. 반환값은
    (headers, user_id, email)."""
    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = register_response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/users/me", headers=headers)
    user_id = me.json()["data"]["user_id"]
    await _make_admin(pool, user_id)
    return headers, user_id, email


async def _register_mfa_admin(client, pool) -> tuple[dict, str]:
    """MFA_VERIFIED 인증 수준인 admin 하나를 실제 등록/설정/로그인 왕복으로
    만든다(값을 흉내 낸 AuthenticatedUser가 아니라 실제 DI 체인)."""
    headers, user_id, email = await _register_admin(client, pool)

    setup_response = await client.post("/auth/mfa/setup", headers=headers)
    secret = setup_response.json()["data"]["secret"]
    verify_code = pyotp.totp.TOTP(secret).now()
    await client.post("/auth/mfa/verify", json={"totp_code": verify_code}, headers=headers)

    # docs/RED_TEAM_FINDINGS.md #13 -- 같은 30초 구간의 코드는 재사용 거부
    # 대상이라, 로그인용 코드는 다음 구간에서 새로 받는다(mfa_clock.py 참조).
    with mfa_clock_shifted(app, 31) as shifted_now:
        login_code = totp_at(secret, shifted_now())
        login_response = await client.post(
            "/auth/login",
            json={"email": email, "password": STRONG_PASSWORD, "totp_code": login_code},
        )
    mfa_token = login_response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {mfa_token}"}, user_id


async def _request_and_approve_grant(
    client, requester_headers: dict, approver_headers: dict, *, scope: str
) -> str:
    request_response = await client.post(
        "/admin/break-glass/grants",
        json={"scope": scope, "reason": "incident-3850", "ttl_minutes": 30},
        headers=requester_headers,
    )
    assert request_response.status_code == 201, request_response.text
    grant_id = request_response.json()["data"]["id"]

    approve_response = await client.post(
        f"/admin/break-glass/grants/{grant_id}:approve", headers=approver_headers
    )
    assert approve_response.status_code == 200, approve_response.text
    assert approve_response.json()["data"]["state"] == "APPROVED"
    return grant_id


# --- request/approve HTTP endpoints -----------------------------------------


async def test_request_grant_requires_mfa_verified_session(client, pool):
    headers, _user_id, _email = await _register_admin(client, pool)

    response = await client.post(
        "/admin/break-glass/grants",
        json={"scope": "tenant_read", "reason": "x", "ttl_minutes": 30},
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "AUTH_MFA_REQUIRED"


async def test_request_and_approve_grant_http_round_trip(client, pool):
    requester_headers, _requester_id = await _register_mfa_admin(client, pool)
    approver_headers, _approver_id = await _register_mfa_admin(client, pool)

    grant_id = await _request_and_approve_grant(
        client, requester_headers, approver_headers, scope="tenant_read"
    )
    assert grant_id


async def test_request_grant_rejects_stale_mfa_step_up_through_http(client, pool):
    """게이트 적색 재현(MFA-bypass 시나리오, task-6482 DoD) -- task-3795가 잡은
    결함을 실제 HTTP 경로에서 재현한다. `auth_level="MFA_VERIFIED"` 클레임만
    보는 이전 게이트였다면, TOTP를 16분 전에 통과해 JWT는 여전히
    "MFA_VERIFIED"를 들고 있는 이 admin의 요청이 그대로 통과했을 것이다 --
    `mfa_verified_at`을 직접 과거로 되돌려(실제 refresh 왕복을 여러 번 거치는
    대신, 그 결과 상태를 직접 재현) 신선도 재검사가 실제로 403을 낸다는 것을
    증명한다."""
    headers, user_id = await _register_mfa_admin(client, pool)

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET mfa_verified_at = now() - interval '16 minutes' "
            "WHERE user_id = $1",
            uuid.UUID(user_id),
        )

    response = await client.post(
        "/admin/break-glass/grants",
        json={"scope": "tenant_read", "reason": "x", "ttl_minutes": 30},
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "AUTH_MFA_REQUIRED"


async def test_approve_grant_self_approval_rejected_through_http(client, pool):
    headers, _user_id = await _register_mfa_admin(client, pool)

    request_response = await client.post(
        "/admin/break-glass/grants",
        json={"scope": "tenant_read", "reason": "x", "ttl_minutes": 30},
        headers=headers,
    )
    grant_id = request_response.json()["data"]["id"]

    approve_response = await client.post(
        f"/admin/break-glass/grants/{grant_id}:approve", headers=headers
    )

    assert approve_response.status_code == 403
    assert approve_response.json()["error_code"] == "AUTHZ_FORBIDDEN"


# --- /admin/audit-log (tenant_read) -----------------------------------------


async def test_audit_log_route_requires_mfa_verified_admin(client, pool):
    """이 leaf가 고친 핵심 DoD: "admin 라우트 MFA 미달 403"이 함수 직접
    호출이 아니라 실제 라우트에서 재현된다."""
    headers, _user_id, _email = await _register_admin(client, pool)

    response = await client.get("/admin/audit-log", headers=headers)

    assert response.status_code == 403
    assert response.json()["error_code"] == "AUTH_MFA_REQUIRED"


async def test_audit_log_route_requires_break_glass_header(client, pool):
    headers, _user_id = await _register_mfa_admin(client, pool)

    response = await client.get("/admin/audit-log", headers=headers)

    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_audit_log_route_rejects_nonexistent_grant(client, pool):
    headers, _user_id = await _register_mfa_admin(client, pool)

    response = await client.get(
        "/admin/audit-log", headers={**headers, "X-Break-Glass-Grant": str(uuid4())}
    )

    assert response.status_code == 409
    assert response.json()["error_code"] == "STATE_INVALID_TRANSITION"


async def test_audit_log_route_rejects_scope_mismatch_grant(client, pool):
    requester_headers, _requester_id = await _register_mfa_admin(client, pool)
    approver_headers, _approver_id = await _register_mfa_admin(client, pool)
    reader_headers, _reader_id = await _register_mfa_admin(client, pool)

    grant_id = await _request_and_approve_grant(
        client, requester_headers, approver_headers, scope="credential_revoke"
    )

    response = await client.get(
        "/admin/audit-log", headers={**reader_headers, "X-Break-Glass-Grant": grant_id}
    )

    assert response.status_code == 409
    assert response.json()["error_code"] == "STATE_INVALID_TRANSITION"


async def test_audit_log_route_succeeds_with_approved_grant_and_grant_is_single_use(client, pool):
    requester_headers, _requester_id = await _register_mfa_admin(client, pool)
    approver_headers, _approver_id = await _register_mfa_admin(client, pool)
    reader_headers, _reader_id = await _register_mfa_admin(client, pool)

    grant_id = await _request_and_approve_grant(
        client, requester_headers, approver_headers, scope="tenant_read"
    )

    first = await client.get(
        "/admin/audit-log", headers={**reader_headers, "X-Break-Glass-Grant": grant_id}
    )
    assert first.status_code == 200, first.text

    # 105번 표준(단일 소비) -- 같은 grant를 다시 제시하면 이미 USED라 거부된다.
    second = await client.get(
        "/admin/audit-log", headers={**reader_headers, "X-Break-Glass-Grant": grant_id}
    )
    assert second.status_code == 409
    assert second.json()["error_code"] == "STATE_INVALID_TRANSITION"


@pytest.mark.perf
async def test_audit_log_route_round_trip_latency_budget(client, pool):
    """관리자 조회치고 관대한 예산(p95 아래 기준으로 단일 왕복 1초) --
    실 DB + 실 HTTP 스택을 타는 통합테스트라 break_glass 코어 자체의
    100ms 예산(test_break_glass.py::test_consume_latency_budget)보다
    넉넉하게 잡는다."""
    requester_headers, _requester_id = await _register_mfa_admin(client, pool)
    approver_headers, _approver_id = await _register_mfa_admin(client, pool)
    reader_headers, _reader_id = await _register_mfa_admin(client, pool)

    grant_id = await _request_and_approve_grant(
        client, requester_headers, approver_headers, scope="tenant_read"
    )

    started = time.perf_counter()
    response = await client.get(
        "/admin/audit-log", headers={**reader_headers, "X-Break-Glass-Grant": grant_id}
    )
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert elapsed < 1.0, f"GET /admin/audit-log elapsed={elapsed * 1000:.1f}ms, 예산 1000ms 초과"


async def test_consume_failure_during_http_request_rolls_back_grant_state(
    client, pool, monkeypatch
):
    """실패 주입 -- HTTP 경로로 들어와도 `require_break_glass`가 여는
    `conn.transaction()` 안에서 consume()이 실제 DB 오류로 실패하면
    grant가 APPROVED로 그대로 남는다(반쯤 USED로 새지 않는다). 코어
    자체의 롤백은 test_break_glass.py::test_audit_failure_rolls_back_approval
    이 이미 증명했다 -- 여기서는 그 보장이 라우터 배선을 거쳐도 깨지지
    않는지를 증명한다."""
    requester_headers, _requester_id = await _register_mfa_admin(client, pool)
    approver_headers, _approver_id = await _register_mfa_admin(client, pool)
    reader_headers, _reader_id = await _register_mfa_admin(client, pool)

    grant_id = await _request_and_approve_grant(
        client, requester_headers, approver_headers, scope="tenant_read"
    )

    from src.api import admin_deps

    async def _failing_consume(conn, *, grant_id, admin_id):
        await conn.execute("INSERT INTO no_such_table_injected_failure (id) VALUES (1)")

    monkeypatch.setattr(admin_deps.break_glass, "consume", _failing_consume)

    response = await client.get(
        "/admin/audit-log", headers={**reader_headers, "X-Break-Glass-Grant": grant_id}
    )
    assert response.status_code == 500

    monkeypatch.undo()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT state, used_at FROM break_glass_grant WHERE id = $1", uuid.UUID(grant_id)
        )
    assert row["state"] == "APPROVED"
    assert row["used_at"] is None


# --- /admin/ledger/payouts/{batch_id}/paid (tenant_read, best-fit) ----------


async def test_ledger_payout_paid_route_requires_mfa_verified_admin(client, pool):
    headers, _user_id, _email = await _register_admin(client, pool)

    response = await client.post(
        f"/admin/ledger/payouts/{uuid4()}/paid",
        json={"external_ref": "ext-1"},
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "AUTH_MFA_REQUIRED"


async def test_ledger_payout_paid_route_requires_break_glass_header(client, pool):
    headers, _user_id = await _register_mfa_admin(client, pool)

    response = await client.post(
        f"/admin/ledger/payouts/{uuid4()}/paid",
        json={"external_ref": "ext-1"},
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


# --- /v1/foundation/risk-gate/safety-controls/{id}:evaluate-recovery -------
# (kill_switch_override)


async def test_risk_gate_recovery_route_requires_mfa_verified_admin(client, pool):
    headers, _user_id, _email = await _register_admin(client, pool)

    response = await client.post(
        f"/v1/foundation/risk-gate/safety-controls/{uuid4()}:evaluate-recovery",
        json={"approval_id": 1},
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "AUTH_MFA_REQUIRED"


async def test_risk_gate_recovery_route_requires_break_glass_header(client, pool):
    headers, _user_id = await _register_mfa_admin(client, pool)

    response = await client.post(
        f"/v1/foundation/risk-gate/safety-controls/{uuid4()}:evaluate-recovery",
        json={"approval_id": 1},
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


# --- gate-red reproduction ----------------------------------------------


async def test_unfixed_admin_route_still_allows_non_mfa_admin_showing_the_original_gap(
    client, pool
):
    """QA(task-3794)가 원래 잡은 결함(get_current_admin만으로는 MFA를
    강제하지 않음)이, 이 leaf가 고치지 않은 라우트에는 여전히 열려 있음을
    보여준다 -- CTO 결정(15+개 라우트 전환은 후속 leaf)의 경계를 그대로
    재현한다. `/admin/users`는 이 leaf의 DoD 밖(files 목록에 없음)이라
    require_break_glass가 없다."""
    headers, _user_id, _email = await _register_admin(client, pool)

    response = await client.get("/admin/users", headers=headers)

    assert response.status_code == 200
