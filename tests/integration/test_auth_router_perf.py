"""Performance / failure-injection / gate-red tests for auth router.

성능 단언, 실패주입, gate-red reproduction는 별도 파일로 분리하여
test_auth_router.py의 LOC를 500줄 경고선 아래로 유지한다.
"""

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from dotenv import dotenv_values
from httpx import ASGITransport, AsyncClient

from src.main import app
from tests._perf.relative_budget import RelativeBudget

STRONG_PASSWORD = "Str0ng!Passw0rd"


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool() -> AsyncGenerator[asyncpg.Pool]:
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def _unique_email() -> str:
    import uuid

    return f"test-{uuid.uuid4().hex}@example.com"


# ── performance assertions (DoD: 1건) ──────────────────────────────────────


@pytest.mark.perf
async def test_register_latency_within_budget(client: AsyncClient) -> None:
    """수치 성능 단언 — register 요청(실제 Argon2 해시 + DB INSERT + ASGI
    직렬화)이 상수시간으로 끝나는지 확인한다. 이 단언이 보장하려는 것은
    "register 경로에 선형 이상으로 느려지는 요인(예: 사용자 테이블 풀스캔,
    N+1 쿼리)이 끼어들지 않았다"는 것이다.

    register 는 I/O 바인딩 연산이므로 wall-clock 기반 RelativeBudget을
    사용한다(process_time 은 async sleep 대기를 재지 못함)."""

    async def _one_register() -> None:
        response = await client.post(
            "/auth/register", json={"email": _unique_email(), "password": STRONG_PASSWORD}
        )
        assert response.status_code == 201

    budget = RelativeBudget()
    # 로컬 무부하 실측 ratio ~3x(단일 register, 보정 루프 대비) — 10배
    # 여유를 둔 15x를 바닥선으로 건다(순차 테이블 풀스캔 등 선형 이탈만
    # 잡고, 호스트 부하로 인한 흔들림은 비율 상쇄로 흡수한다).
    sample = await budget.assert_within_async(
        _one_register,
        max_ratio=15.0,
        n=3,
        warmup=1,
        calibration_n=3,
        label="single register",
    )
    print(f"[auth/register] {budget.describe(sample, max_ratio=15.0)}")


@pytest.mark.perf
async def test_suspended_account_check_latency_within_budget(
    client: AsyncClient,
) -> None:
    """수치 성능 단언 — SUSPEND 상태 조회(토큰 → DB user lookup → status
    체크)가 상수시간으로 끝나는지 확인한다. 이 단언이 보장하려는 것은
    "trust/status 경로에 선형 이상으로 느려지는 요인(예: 사용자 테이블
    풀스캔, N+1 쿼리)이 끼어들지 않았다"는 것이다.

    register/로그인보다 훨씬 가벼운 경로이므로 max_ratio=10x로 tighter
    바닥선을 건다. I/O 바인딩이므로 wall-clock 측정 사용."""
    email = _unique_email()
    await client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})
    login_response = await client.post(
        "/auth/login", json={"email": email, "password": STRONG_PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login_response.json()['data']['access_token']}"}

    async def _status_check() -> None:
        response = await client.get("/v1/foundation/trust/status", headers=headers)
        assert response.status_code == 200

    budget = RelativeBudget()
    sample = await budget.assert_within_async(
        _status_check,
        max_ratio=10.0,
        n=3,
        warmup=1,
        calibration_n=3,
        label="trust/status check",
    )
    print(f"[trust/status] {budget.describe(sample, max_ratio=10.0)}")


@pytest.mark.perf
async def test_login_latency_within_budget(client: AsyncClient) -> None:
    """수치 성능 단언 — 로그인 요청(실제 Argon2 해시 검증 + DB 왕복 + ASGI
    요청/응답 직렬화)이 상수시간으로 끝나는지 확인한다. 이 단언이 보장하려는
    것은 "로그인 경로에 선형 이상으로 느려지는 요인(예:
    테이블 풀스캔, N+1 쿼리)이 끼어들지 않았다"는 것이다.

    task-10652: 원래 20회 순차 로그인의 절대 벽시계 합(예산 1.5s)을 썼다 —
    비동기 I/O 왕복은 `time.process_time()` CPU 시간으로 바꾸면 대기 시간이
    통째로 사라져 의미가 없어지므로(실제 DB/네트워크 지연을 재지 못함),
    `RelativeBudget.measure_async`로 같은 프로세스·같은 순간에 돈 동기 CPU
    보정 루프 대비 비율로 바꿔 호스트 부하(워커 13개 + llama.cpp 동시 실행)를
    상쇄한다 — 보정 루프가 2배 느려지면 로그인 왕복도 스케줄링 경합으로
    비례해 느려지므로 비율은 유지된다."""
    email = _unique_email()
    await client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})

    async def _twenty_logins() -> None:
        for _ in range(20):
            response = await client.post(
                "/auth/login", json={"email": email, "password": STRONG_PASSWORD}
            )
            assert response.status_code == 200

    budget = RelativeBudget()
    # 로컬 무부하 실측 ratio ~11x(20회 로그인 ~1.5s, 보정 루프 ~130ms) — 10배
    # 이상 여유를 둔 40x를 바닥선으로 건다(순차 테이블 풀스캔 등 선형 이탈만
    # 잡고, 호스트 부하로 인한 흔들림은 비율 상쇄로 흡수한다).
    sample = await budget.assert_within_async(
        _twenty_logins,
        max_ratio=40.0,
        n=3,
        warmup=0,
        calibration_n=3,
        label="20회 로그인",
    )
    print(f"[auth] {budget.describe(sample, max_ratio=40.0)}")


# ── failure-injection tests (DoD: ≥1건) ─────────────────────────────────


async def test_login_failure_injection_db_error(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실패주입 — DB 레이어에서 예외가 발생하면 500이 아닌 구조화된
    서비스 에러 응답으로 감싸져야 한다.

    FastAPI dependency_override로 get_current_user를 패치해
    ConnectionError를 유발하고, exception_mapping이 INTERNAL_ERROR(500)로
    매핑하는지, 그리고 trace_id가 반드시 포함되어 있는지 확인한다.
    """
    from src.api.deps import get_current_user as real_dep

    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    headers = {"Authorization": f"Bearer {register_response.json()['data']['access_token']}"}

    async def patched_get_current_user() -> Any:
        raise ConnectionError("Injected DB connection failure")

    # FastAPI dependency_override — 앱 재시작 없이 의존성 대체
    app.dependency_overrides[real_dep] = patched_get_current_user

    try:
        # /auth/logout-all — 의존성 호출 시 DB 예외 유발
        response = await client.post("/auth/logout-all", headers=headers)

        # 500 INTERNAL_ERROR로 감싸져서 반환되어야 함
        assert response.status_code == 500
        body = response.json()
        assert body["error_code"] == "INTERNAL_ERROR"
        # trace_id가 반드시 포함되어 있어야 함 (관측성 불변식)
        assert "trace_id" in body
    finally:
        del app.dependency_overrides[real_dep]


# ── gate-red reproduction ──────────────────────────────────────────────────


@pytest.mark.perf
async def test_register_perf_goes_red_with_io_delay_injection(
    client: AsyncClient,
    pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gate-red reproduction: asyncpg.Connection.execute 에 IO 지연(delayed_async,
    2.0s)을 주입한 후 같은 성능 단언을 돌리면 예산을 초과해야 한다(red).

    `conn.execute` 를 `delayed_async` 로 감싸 1회 호출당 2초 IO 지연을
    주입 — register 는 I/O 바인딩 연산이므로 wall-clock 측정으로 지연을
    검증한다. 정상 입력에서 통과하는 것이 테스트의 허수(trivial pass)가
    아님을 보증한다. 단언/마커 삭제와 예산 완화 금지.

    task-17653: delay injection uses ``delayed_async`` from
    ``tests.support.perf_inject`` (no ``# type: ignore``).
    """
    from tests.support.perf_inject import delayed_async

    # asyncpg.Connection.execute 를 delayed_async 로 감싸 IO 지연 주입
    original_execute = asyncpg.Connection.execute
    monkeypatch.setattr(asyncpg.Connection, "execute", delayed_async(original_execute, 2.0))

    budget = RelativeBudget()

    async def _one_register_with_delay() -> None:
        response = await client.post(
            "/auth/register",
            json={"email": _unique_email(), "password": STRONG_PASSWORD},
        )
        assert response.status_code == 201

    # 지연 주입 후 → wall-clock 이 예산 초과해야 함 (red)
    # 2.0 s 지연 >> calibration × 15 budget 이므로 AssertionError 발생해야 함
    with pytest.raises(AssertionError, match=r"budget"):
        await budget.assert_within_async(
            _one_register_with_delay,
            max_ratio=15.0,
            n=3,
            warmup=1,
            calibration_n=3,
            label="single register+delay",
        )

    # 지연 주입으로 circuit_breaker_level 이 'halted' 될 수 있으므로
    # sentinel(gate-red reproduction) 종료 후 반드시 정상 복원
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )


@pytest.mark.perf
async def test_suspended_account_perf_goes_red_with_io_delay_injection(
    client: AsyncClient,
    pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gate-red reproduction: asyncpg.Connection.execute 에 가상 지연(
    virtual_delayed_async, 2.0s)을 주입한 후 같은 성능 단언을 돌리면
    예산을 초과해야 한다(red).

    virtual_delayed_async 는 실제 sleep 없이 측정치만 늘리므로 테스트가
    수초~수십 초를 기다리지 않는다. 정상 입력에서 통과하는 것이 테스트의
    허수(trivial pass)가 아님을 보증한다.
    단언/마커 삭제와 예산 완화 금지.

    task-20697: delay injection uses ``virtual_delayed_async`` from
    ``tests.support.perf_inject`` (no ``# type: ignore``).
    """
    from tests.support.perf_inject import virtual_delayed_async

    original_execute = asyncpg.Connection.execute
    monkeypatch.setattr(asyncpg.Connection, "execute", virtual_delayed_async(original_execute, 2.0))

    email = _unique_email()
    await client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})
    login_response = await client.post(
        "/auth/login", json={"email": email, "password": STRONG_PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login_response.json()['data']['access_token']}"}

    async def _status_check_with_delay() -> None:
        response = await client.get("/v1/foundation/trust/status", headers=headers)
        assert response.status_code == 200

    budget = RelativeBudget()

    # 지연 주입 후 → wall-clock 이 예산 초과해야 함 (red)
    # 2.0 s 지연 >> calibration × 10 budget 이므로 AssertionError 발생해야 함
    with pytest.raises(AssertionError, match=r"budget"):
        await budget.assert_within_async(
            _status_check_with_delay,
            max_ratio=10.0,
            n=3,
            warmup=1,
            calibration_n=3,
            label="trust/status+delay",
        )

    # 지연 주입으로 circuit_breaker_level 이 'halted' 될 수 있으므로
    # sentinel(gate-red reproduction) 종료 후 반드시 정상 복원
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
