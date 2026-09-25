"""PLT-22 통합테스트 — 로그인 실패 잠금 원자화(task-852).

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-22

DoD: asyncio.gather로 동시 10회 실패 로그인 시 카운트가 정확히 10(경합
손실 0)이고, 임계 초과 후 423(AccountLockedError) + retry_after가
나오며, 성공 로그인이 카운터를 초기화함을 실DB로 단언한다.

DEEPEN(task-3148): task-852 DEPTH 감사가 실패 주입(monkeypatch/MagicMock)과
수치 성능 단언이 없다고 지적함 — 새 기능 추가 없이 이 리프의 증빙만
보강한다. 실패 주입은 (1) `register_failed_attempt`가 DB 계층 실패
(커넥션 끊김)를 삼켜 "미잠금"으로 오판하지 않는지, (2) `authenticate`의
`_fail_login` 경로가 lockout 백엔드 실패를 AuthError(401)로 흡수해버리지
않는지를 확인한다 — 둘 다 fail-closed 회귀 방지용이다. 성능 단언은
ADR-2026-09-09-C Decision 1 예산표에 로그인 실패 카운터 전용 항목이 없어
(가장 가까운 항목은 "주문 제출→ACK p95 50ms(paper)") 그 값을 자체 예산으로
차용한다 — 둘 다 단일 실DB 왕복(UPDATE...RETURNING)이라는 점에서 비교
가능한 부하 특성을 가진다.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.auth import lockout
from src.services.auth_service import AccountLockedError, AuthError, AuthService

JWT_SECRET = "test-secret-key-not-for-production"
STRONG_PASSWORD = "Str0ng!Passw0rd"


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=12)
    yield p
    await p.close()


@pytest.fixture
def auth(pool):
    return AuthService(pool, jwt_secret_key=JWT_SECRET)


def _unique_email() -> str:
    return f"test-lockout-{uuid.uuid4().hex}@example.com"


async def _signed_up_user_id(auth: AuthService, pool: asyncpg.Pool) -> tuple[str, uuid.UUID]:
    email = _unique_email()
    user = await auth.signup(email, STRONG_PASSWORD)
    return email, user.user_id


async def _failed_attempts(pool: asyncpg.Pool, user_id: uuid.UUID) -> int:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT failed_login_attempts FROM users WHERE user_id = $1", user_id
        )
    return int(value)


# --- lockout.py 원자 증가 자체 — DB 경합 손실 0 증명 ------------------------


async def test_concurrent_register_failed_attempt_has_no_lost_updates(auth, pool):
    """10개 커넥션에서 동시에 같은 user_id에 register_failed_attempt를
    호출해도 UPDATE ... RETURNING이 행 잠금으로 직렬화되므로, 반환된
    failed_attempts 값 10개는 1..10 전체 집합과 정확히 일치해야 한다
    (중복도, 결손도 없음 = 경합 손실 0)."""
    _, user_id = await _signed_up_user_id(auth, pool)

    async def attempt():
        async with pool.acquire() as conn:
            return await lockout.register_failed_attempt(conn, user_id)

    states = await asyncio.gather(*(attempt() for _ in range(10)))

    assert sorted(s.failed_attempts for s in states) == list(range(1, 11))
    assert await _failed_attempts(pool, user_id) == 10

    locked_states = [s for s in states if s.locked]
    # MAX_FAILED_ATTEMPTS=5이므로 5번째~10번째 도달분(6개)이 잠금 상태다.
    assert len(locked_states) == 10 - lockout.MAX_FAILED_ATTEMPTS + 1
    for state in locked_states:
        assert state.retry_after_seconds is not None
        assert state.retry_after_seconds > 0


async def test_register_failed_attempt_for_unknown_user_raises(pool):
    """negative: 존재하지 않는 user_id로 호출하면 조용히 0행 UPDATE로
    끝나지 않고 명시적으로 실패한다(fail-closed)."""
    with pytest.raises(ValueError):
        async with pool.acquire() as conn:
            await lockout.register_failed_attempt(conn, uuid.uuid4())


# --- AuthService.authenticate() 경유 — 423 + retry_after 계약 ---------------


async def test_concurrent_failed_logins_lock_and_expose_retry_after(auth, pool):
    """DoD 시나리오 그대로: 동시 10회 실패 로그인 → 카운트 정확히 10,
    임계 초과분은 AccountLockedError(423 계약)로 retry_after_seconds를
    노출한다."""
    email, user_id = await _signed_up_user_id(auth, pool)

    async def attempt():
        try:
            await auth.authenticate(email, "WrongPassword1!")
        except AuthError as exc:
            return exc
        raise AssertionError("wrong password는 항상 AuthError를 던져야 한다")

    results = await asyncio.gather(*(attempt() for _ in range(10)))

    assert await _failed_attempts(pool, user_id) == 10

    locked = [r for r in results if isinstance(r, AccountLockedError)]
    assert len(locked) >= 1
    for exc in locked:
        assert exc.error_code == "AUTH_ACCOUNT_LOCKED"
        assert exc.http_status == 423
        assert exc.retry_after_seconds is not None
        assert 0 < exc.retry_after_seconds <= lockout.LOCKOUT_MINUTES * 60


async def test_already_locked_account_rejects_even_correct_password_with_retry_after(auth, pool):
    email, user_id = await _signed_up_user_id(auth, pool)
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET failed_login_attempts = 4 WHERE user_id = $1", user_id)

    with pytest.raises(AccountLockedError) as exc_info:
        await auth.authenticate(email, "WrongPassword1!")
    assert exc_info.value.retry_after_seconds is not None

    with pytest.raises(AccountLockedError) as exc_info_locked:
        await auth.authenticate(email, STRONG_PASSWORD)
    assert exc_info_locked.value.retry_after_seconds is not None
    assert exc_info_locked.value.retry_after_seconds > 0


# --- 성공 로그인이 카운터를 초기화 -----------------------------------------


async def test_successful_login_resets_failed_attempts_and_lock(auth, pool):
    email, user_id = await _signed_up_user_id(auth, pool)
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET failed_login_attempts = 3 WHERE user_id = $1", user_id)

    await auth.authenticate(email, STRONG_PASSWORD)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT failed_login_attempts, locked_until FROM users WHERE user_id = $1", user_id
        )
    assert row["failed_login_attempts"] == 0
    assert row["locked_until"] is None


# --- 순수 함수 retry_after_seconds — 경계값 ---------------------------------


def test_retry_after_seconds_pure_boundaries():
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    assert lockout.retry_after_seconds(None, now) is None
    assert lockout.retry_after_seconds(now, now) is None
    assert lockout.retry_after_seconds(now - timedelta(seconds=1), now) is None
    assert lockout.retry_after_seconds(now + timedelta(seconds=90), now) == 90
    # 반올림 오차로 0초가 나가지 않도록 최소 1초 보장.
    assert lockout.retry_after_seconds(now + timedelta(milliseconds=200), now) == 1


# --- DEEPEN(task-3148): 실패 주입 — DB 계층 실패가 삼켜지지 않는지 ---------


class _BoomConnection:
    """asyncpg.Connection 대역 — fetchrow 호출 시 DB 계층 실패(예: 커넥션
    끊김)를 시뮬레이션한다. register_failed_attempt가 이를 삼켜 '증가
    실패=미잠금'으로 오판하면 안 되고(fail-closed), 원 예외를 그대로
    전파해야 한다."""

    async def fetchrow(self, *args, **kwargs):
        raise asyncpg.PostgresConnectionError("simulated connection loss")


async def test_register_failed_attempt_propagates_db_failure_instead_of_swallowing():
    """실패 주입: UPDATE ... RETURNING 왕복 자체가 asyncpg 커넥션 계층에서
    실패하면(예: 커넥션 끊김) register_failed_attempt는 이를 삼켜 '잠기지
    않음'으로 위장하지 않고 그대로 전파해야 한다 — 삼키면 실패 카운터가
    실제로는 증가하지 않았는데도 로그인 시도가 조용히 통과해버리는
    fail-open 결함이 된다."""
    with pytest.raises(asyncpg.PostgresConnectionError, match="simulated connection loss"):
        await lockout.register_failed_attempt(_BoomConnection(), uuid.uuid4())


async def test_fail_login_propagates_lockout_backend_failure_instead_of_401(
    auth, pool, monkeypatch
):
    """실패 주입: authenticate()가 위임하는 _fail_login 경로에서
    lockout.register_failed_attempt 자체가 실패하면(monkeypatch로 시뮬레이션),
    이를 흔한 401 AuthError로 흡수해 위장하지 않고 원 예외를 그대로
    전파해야 한다 — 잠금 판정 실패를 '그냥 로그인 실패'로 뭉개면 카운터가
    갱신되지 않은 채로도 클라이언트는 재시도해도 되는 것처럼 보이게 된다
    (fail-closed 회귀 방지)."""
    email, _ = await _signed_up_user_id(auth, pool)

    async def _boom(conn, user_id, *, now=None):
        raise RuntimeError("simulated lockout backend failure")

    monkeypatch.setattr(lockout, "register_failed_attempt", _boom)

    with pytest.raises(RuntimeError, match="simulated lockout backend failure"):
        await auth.authenticate(email, "WrongPassword1!")


# --- DEEPEN(task-3148): 수치 성능 단언 — register_failed_attempt 왕복 지연 --

_PERF_ITERATIONS = 30
_PERF_BUDGET_MS = 50.0  # ADR-2026-09-09-C Decision 1의 "주문 제출→ACK p95
# 50ms(paper)"를 가장 가까운 유사 항목으로 차용(로그인 실패 카운터 전용
# 예산 항목 없음) — 둘 다 단일 실DB 왕복(UPDATE...RETURNING)이라는 점에서
# 비교 가능한 부하 특성을 가진다.


async def _register_attempt_latencies_ms(
    pool: asyncpg.Pool, user_id: uuid.UUID, iterations: int = _PERF_ITERATIONS
) -> list[float]:
    samples = []
    async with pool.acquire() as conn:
        for _ in range(iterations):
            started = time.perf_counter()
            await lockout.register_failed_attempt(conn, user_id)
            samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


async def test_register_failed_attempt_p95_latency_within_self_declared_budget(auth, pool):
    _, user_id = await _signed_up_user_id(auth, pool)
    samples = await _register_attempt_latencies_ms(pool, user_id)
    p95_ms = _p95(samples)
    print(f"[PLT-22] register_failed_attempt p95={p95_ms:.3f}ms budget<{_PERF_BUDGET_MS:.0f}ms")
    assert p95_ms < _PERF_BUDGET_MS


async def test_register_failed_attempt_budget_gate_actually_fails_past_budget(
    auth, pool, monkeypatch
):
    """게이트 적색 재현: 위 단언식이 실제 지연 주입에 대해 AssertionError를
    내는지 확인한다 — 이 단언이 항상 통과하는 tautology가 아님을 증명한다."""
    _, user_id = await _signed_up_user_id(auth, pool)

    original_fetchrow = asyncpg.Connection.fetchrow

    async def _slow_fetchrow(self, *args, **kwargs):
        await asyncio.sleep(_PERF_BUDGET_MS / 1000.0)
        return await original_fetchrow(self, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", _slow_fetchrow)

    samples = await _register_attempt_latencies_ms(pool, user_id, iterations=3)
    p95_ms = _p95(samples)
    with pytest.raises(AssertionError):
        assert p95_ms < _PERF_BUDGET_MS
