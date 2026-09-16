"""DEEPEN task-3178 — task-1189(MFA 재설정 레이스 수정, commit 0e77d6b6)의
D2 하한 증빙 보강.

원 리프(tests/integration/test_auth_router.py::
test_mfa_resetup_without_password_rejected_when_already_enabled 등)는 실 DB +
실 HTTP 왕복으로 해피패스·negative를 갖췄지만, 레드팀 감사 #11 가드
(`이미 활성화된 MFA는 비밀번호 재인증 없이 재설정 불가`, src/api/routers/
auth.py:setup_mfa)를 직접 겨냥한 monkeypatch 기반 실패 주입, 수치 성능
단언, 게이트 적색 재현이 없었다(ADR-2026-09-09-C Decision 1). 새 기능
추가 없이 이 가드의 증빙만 채운다.

D3 여부: 이 리프의 spec id(PLT-17~21/PLT-24)는 ADR-2026-09-09-C D3 축
목록(R/L4/LA/LB/LC/FA/CM/EO/DC)에 없다 — D2 floor만 적용한다.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import asyncpg
import pytest

from src.api.deps import reauthenticate
from src.main import app
from src.services.auth_service import AuthService
from tests.integration.mfa_clock import mfa_clock_frozen, totp_at
from tests.integration.test_auth_router import STRONG_PASSWORD, _asyncpg_dsn, _unique_email


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


@pytest.fixture
async def client():
    from httpx import ASGITransport, AsyncClient

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


async def _register_and_enable_mfa(client, pool) -> tuple[str, str, datetime]:
    """가입 + MFA 최초 설정/검증까지 마친 (email, secret, frozen_now)를 반환한다."""
    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = register_response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    frozen_now = datetime.now(timezone.utc)
    with mfa_clock_frozen(app, frozen_now):
        setup_response = await client.post("/auth/mfa/setup", headers=headers)
        secret = setup_response.json()["data"]["secret"]
        code = totp_at(secret, frozen_now)
        await client.post("/auth/mfa/verify", json={"totp_code": code}, headers=headers)

    return token, secret, frozen_now


async def _mfa_secret_in_db(pool: asyncpg.Pool, email: str) -> str | None:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT mfa_secret FROM users WHERE email = $1", email)


# --- 실패 주입(monkeypatch) ------------------------------------------------


async def test_mfa_resetup_stays_rejected_when_reauth_raises_unexpected_error(
    client, pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """레드팀 감사 #11 가드가 "예상한 실패"(비밀번호 오류 → 403)뿐 아니라
    "예상 밖 실패"(예: bcrypt 라이브러리 버그, DB 커넥션 드롭 같은 인프라
    예외)에도 fail-closed인지 확인한다. `AuthService.authenticate`를
    monkeypatch해 `AuthError`가 아닌 예상 밖 `RuntimeError`를 흉내내고,
    (1) 응답이 200이 아니고, (2) `mfa.setup()`이 호출되지 않아 DB의
    `mfa_secret`이 그대로인지를 함께 확인한다 — 예외를 삼키고 조용히
    `mfa.setup()`으로 흘러가면 공격자가 인프라 노이즈를 유발해 재설정 가드를
    우회할 수 있다."""
    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = register_response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    frozen_now = datetime.now(timezone.utc)
    with mfa_clock_frozen(app, frozen_now):
        setup_response = await client.post("/auth/mfa/setup", headers=headers)
        original_secret = setup_response.json()["data"]["secret"]
        code = totp_at(original_secret, frozen_now)
        await client.post("/auth/mfa/verify", json={"totp_code": code}, headers=headers)

        secret_before = await _mfa_secret_in_db(pool, email)
        assert secret_before is not None

        async def _boom(self, *args, **kwargs):
            raise RuntimeError("simulated infra failure in authenticate()")

        monkeypatch.setattr(AuthService, "authenticate", _boom)

        resetup_response = await client.post(
            "/auth/mfa/setup",
            json={"password": STRONG_PASSWORD, "totp_code": code},
            headers=headers,
        )

    assert resetup_response.status_code != 200
    secret_after = await _mfa_secret_in_db(pool, email)
    assert secret_after == secret_before


async def test_reauthenticate_does_not_swallow_unexpected_exception(monkeypatch):
    """위 HTTP 왕복 테스트와 대칭인 단위 수준 증빙 — `src/api/deps.py::
    reauthenticate`는 `AuthError`만 403으로 변환한다(§L4-24 설계). `authenticate`가
    그 외 예외를 던지면 그대로 전파해야지, 광범위한 `except Exception`으로
    삼켜 재인증을 "통과"처럼 보이게 하면 안 된다."""

    class _FakeAuthService:
        async def authenticate(self, *args, **kwargs):
            raise RuntimeError("simulated infra failure")

    class _FakeUser:
        email = "unused@example.com"

    with pytest.raises(RuntimeError):
        await reauthenticate(_FakeAuthService(), _FakeUser(), "irrelevant-password")


# --- 수치 성능 단언 ---------------------------------------------------------

_REJECTION_BUDGET_MS = 300.0


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)]


async def test_mfa_resetup_rejection_p95_latency_budget(client):
    """수치 성능 단언 — 비밀번호 없이 재설정을 시도하는 공격 트래픽은 실제
    비밀번호 검증(bcrypt)이나 `mfa.setup()`(TOTP secret 생성) 없이 즉시
    403이어야 한다(src/api/routers/auth.py:132 `if not body.password: raise`
    가 `reauthenticate()`/`mfa.setup()` 호출보다 먼저 있음). ADR-2026-09-09-C
    Decision 1 예산표에는 이 경로 전용 항목이 없어 자체 예산을 건다 — HTTP
    왕복(ASGITransport, 로그인+MFA 최초설정 제외한 재설정-거부 단독 구간)
    로컬 실측 p95는 수 ms대(2026-09-17)이고, CI 변동을 감안해 그 대비 넉넉한
    여유를 둔 300ms."""
    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = register_response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    frozen_now = datetime.now(timezone.utc)
    with mfa_clock_frozen(app, frozen_now):
        setup_response = await client.post("/auth/mfa/setup", headers=headers)
        secret = setup_response.json()["data"]["secret"]
        code = totp_at(secret, frozen_now)
        await client.post("/auth/mfa/verify", json={"totp_code": code}, headers=headers)

        samples: list[float] = []
        for _ in range(20):
            started = time.perf_counter()
            response = await client.post("/auth/mfa/setup", headers=headers)
            samples.append((time.perf_counter() - started) * 1000)
            assert response.status_code == 403

    p95_ms = _p95(samples)
    print(f"[task-3178] MFA 재설정 즉시거부 p95={p95_ms:.2f}ms budget<{_REJECTION_BUDGET_MS:.0f}ms")
    assert p95_ms < _REJECTION_BUDGET_MS


async def test_perf_budget_gate_actually_fails_when_rejection_path_stalls(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 적색 재현 — 거부 경로가 실제로 느려지면(예: `if not
    body.password` 가드가 `mfa.setup()` 뒤로 밀려나는 회귀)
    `test_mfa_resetup_rejection_p95_latency_budget`과 동일한 단언식이 진짜로
    `AssertionError`를 내는지 확인한다."""
    import asyncio

    original_authenticate = AuthService.authenticate

    async def _stalled_authenticate(self, *args, **kwargs):
        await asyncio.sleep(_REJECTION_BUDGET_MS / 1000)
        return await original_authenticate(self, *args, **kwargs)

    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = register_response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    frozen_now = datetime.now(timezone.utc)
    with mfa_clock_frozen(app, frozen_now):
        setup_response = await client.post("/auth/mfa/setup", headers=headers)
        secret = setup_response.json()["data"]["secret"]
        code = totp_at(secret, frozen_now)
        await client.post("/auth/mfa/verify", json={"totp_code": code}, headers=headers)

        # 이 회귀 시나리오에서는 거부 가드가 authenticate() 호출 "뒤"로
        # 밀려났다고 가정 — password를 줘서 그 경로를 타게 만든다.
        monkeypatch.setattr(AuthService, "authenticate", _stalled_authenticate)
        started = time.perf_counter()
        await client.post(
            "/auth/mfa/setup",
            json={"password": STRONG_PASSWORD, "totp_code": code},
            headers=headers,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000

    with pytest.raises(AssertionError):
        assert elapsed_ms < _REJECTION_BUDGET_MS


# --- negative ---------------------------------------------------------------


async def test_mfa_resetup_rejected_with_wrong_password_when_already_enabled(client):
    """이미 켜진 MFA를 잘못된 비밀번호로 재설정 시도 — `reauthenticate()`가
    `AuthError`를 403으로 변환하는지 확인한다(레드팀 감사 #11 가드의 정상
    실패 경로, 예상 밖 예외 케이스는 위 실패 주입 테스트가 담당)."""
    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = register_response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    frozen_now = datetime.now(timezone.utc)
    with mfa_clock_frozen(app, frozen_now):
        setup_response = await client.post("/auth/mfa/setup", headers=headers)
        secret = setup_response.json()["data"]["secret"]
        code = totp_at(secret, frozen_now)
        await client.post("/auth/mfa/verify", json={"totp_code": code}, headers=headers)

        resetup_response = await client.post(
            "/auth/mfa/setup",
            json={"password": "Wrong!Password9", "totp_code": code},
            headers=headers,
        )

    assert resetup_response.status_code == 403


async def test_mfa_resetup_rejected_without_auth_header(client):
    """Bearer 토큰조차 없는 요청은 401 — 인증 자체가 안 된 상태에서 MFA
    재설정 가드까지 도달하지 않는지 확인한다."""
    response = await client.post("/auth/mfa/setup")
    assert response.status_code == 401


async def test_mfa_resetup_rejected_with_empty_string_password(client):
    """빈 문자열 비밀번호는 `not body.password`가 참이 되어 재인증 시도조차
    없이 즉시 403이어야 한다(경계값 — None과 ""가 같은 분기를 타는지)."""
    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = register_response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    frozen_now = datetime.now(timezone.utc)
    with mfa_clock_frozen(app, frozen_now):
        setup_response = await client.post("/auth/mfa/setup", headers=headers)
        secret = setup_response.json()["data"]["secret"]
        code = totp_at(secret, frozen_now)
        await client.post("/auth/mfa/verify", json={"totp_code": code}, headers=headers)

        resetup_response = await client.post(
            "/auth/mfa/setup", json={"password": ""}, headers=headers
        )

    assert resetup_response.status_code == 403
