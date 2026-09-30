"""20번대 통합테스트 — /reports 라우터. 실제 FastAPI 앱 + 실제 dev DB."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values
from httpx import ASGITransport, AsyncClient

from src.api.reports_deps import get_report_service
from src.main import app

STRONG_PASSWORD = "Str0ng!Passw0rd"


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        # raise_app_exceptions=False -- 실패주입 테스트가 전역 Exception
        # 핸들러가 만든 500 응답을 정상적으로 받아야 한다(Starlette가
        # 처리된 예외를 응답 뒤에도 재전파하는 경우가 있다, 다른 통합
        # 테스트 client 픽스처와 동일 근거).
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def _assert_error_envelope(body: dict, code: str) -> None:
    assert body["error_code"] == code
    assert set(body) >= {"error_code", "message", "trace_id"}
    assert "data" not in body


class _OutageReportService:
    """실패주입 모의 서비스 -- 의존 계층(pool/asyncpg)이 커넥션 예외를 던지는
    상황을 흉내낸다. 라우터는 이 예외를 그대로 propagate하고(§L4-20 docstring),
    전역 Exception 핸들러가 500/INTERNAL_ERROR 봉투로 fail-closed 해야 한다."""

    async def generate_report(self, user_id, period_start, period_end, *, execution_id=None):
        raise asyncpg.PostgresConnectionError("simulated report service outage")


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


async def _register(client) -> tuple[dict, str]:
    email = _unique_email()
    response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/users/me", headers=headers)
    return headers, me.json()["data"]["user_id"]


async def _insert_closed_position(pool, user_id, *, realized_pnl, closed_at, strategy_id):
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO positions
                (user_id, symbol, exchange, strategy_id, quantity, average_entry_price,
                 realized_pnl, entry_time, closed_at)
            VALUES ($1, 'BTC/USDT', 'bitget', $2, 0, 100, $3, $4, $4)
            """,
            uuid.UUID(user_id),
            strategy_id,
            realized_pnl,
            closed_at,
        )


async def test_report_empty_period_returns_zeroed_summary(client):
    headers, _ = await _register(client)

    response = await client.get(
        "/reports",
        params={"period_start": "2020-01-01", "period_end": "2020-01-31"},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["trade_count"] == 0
    assert body["win_rate"] is None
    assert body["total_return"] == "0"


async def test_report_aggregates_closed_positions(client, pool):
    headers, user_id = await _register(client)
    today = datetime.now(timezone.utc)
    await _insert_closed_position(
        pool, user_id, realized_pnl=Decimal("100"), closed_at=today, strategy_id="strat-a"
    )
    await _insert_closed_position(
        pool,
        user_id,
        realized_pnl=Decimal("-40"),
        closed_at=today - timedelta(days=1),
        strategy_id="strat-a",
    )

    response = await client.get(
        "/reports",
        params={
            "period_start": (today - timedelta(days=7)).date().isoformat(),
            "period_end": today.date().isoformat(),
        },
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["trade_count"] == 2
    assert Decimal(body["total_return"]) == Decimal("60")
    assert Decimal(body["win_rate"]) == Decimal("50")
    assert len(body["strategy_contributions"]) == 1
    assert body["strategy_contributions"][0]["strategy_id"] == "strat-a"


async def test_report_buckets_by_utc_date_regardless_of_session_timezone(client, pool):
    """Regression guard for task-6301: a closed_at just before UTC midnight rolls to the
    next calendar day under a non-UTC Postgres session TimeZone (this env runs Asia/Seoul,
    UTC+9) unless the query explicitly anchors the ``::date`` cast to UTC. Pin closed_at to
    2020-01-01T20:00:00Z (2020-01-02 05:00 KST) and request a period that only covers
    2020-01-01 UTC -- a session-local cast would bucket the row into 2020-01-02 and drop it.
    """
    headers, user_id = await _register(client)
    closed_at = datetime(2020, 1, 1, 20, 0, 0, tzinfo=timezone.utc)
    strategy_id = f"strat-{uuid.uuid4().hex}"
    await _insert_closed_position(
        pool, user_id, realized_pnl=Decimal("10"), closed_at=closed_at, strategy_id=strategy_id
    )

    response = await client.get(
        "/reports",
        params={"period_start": "2020-01-01", "period_end": "2020-01-01"},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["trade_count"] == 1
    assert body["daily_pnl"][0]["trade_date"] == "2020-01-01"


async def test_reports_require_authentication(client):
    response = await client.get(
        "/reports", params={"period_start": "2020-01-01", "period_end": "2020-01-31"}
    )

    assert response.status_code == 401


async def test_reports_missing_required_params_rejected(client):
    """negative -- period_start/period_end이 불변식(필수 쿼리 파라미터)이다.
    누락 시 서비스나 DB를 전혀 부르지 않고 400/VALIDATION_INVALID_FIELD로 거부한다
    (전역 RequestValidationError 핸들러가 FastAPI 422를 400으로 재매핑한다,
    src/api/contracts/error_codes.py의 VALIDATION_INVALID_FIELD -> 400 고정)."""
    headers, _ = await _register(client)

    response = await client.get("/reports", headers=headers)

    assert response.status_code == 400
    _assert_error_envelope(response.json(), "VALIDATION_INVALID_FIELD")


async def test_reports_invalid_date_format_rejected(client):
    """negative -- ISO 8601 날짜가 아닌 값은 파싱 단계에서 거부되어야 한다
    (예: '2020-13-99'는 존재하지 않는 월/일)."""
    headers, _ = await _register(client)

    response = await client.get(
        "/reports",
        params={"period_start": "2020-13-99", "period_end": "2020-01-31"},
        headers=headers,
    )

    assert response.status_code == 400
    _assert_error_envelope(response.json(), "VALIDATION_INVALID_FIELD")


async def test_reports_non_integer_execution_id_rejected(client):
    """negative -- execution_id는 int | None 타입 불변식을 갖는다. 문자열이
    오면 400으로 거부하고, SQL로 전달되지 않는다(주입 방지 경계 역할 겸함)."""
    headers, _ = await _register(client)

    response = await client.get(
        "/reports",
        params={
            "period_start": "2020-01-01",
            "period_end": "2020-01-31",
            "execution_id": "not-an-int",
        },
        headers=headers,
    )

    assert response.status_code == 400
    _assert_error_envelope(response.json(), "VALIDATION_INVALID_FIELD")


async def test_reports_service_outage_fails_closed_with_generic_500(client):
    """failure-injection -- ReportService 의존 계층이 커넥션 예외를 던지면
    부분 데이터나 200을 흘리지 않고 500/INTERNAL_ERROR 봉투로 fail-closed
    한다. 원인 예외 문자열은 응답 메시지에 새지 않는다."""
    headers, _ = await _register(client)

    app.dependency_overrides[get_report_service] = lambda: _OutageReportService()
    try:
        response = await client.get(
            "/reports",
            params={"period_start": "2020-01-01", "period_end": "2020-01-31"},
            headers=headers,
        )
    finally:
        app.dependency_overrides.pop(get_report_service, None)

    assert response.status_code == 500
    body = response.json()
    _assert_error_envelope(body, "INTERNAL_ERROR")
    assert "PostgresConnectionError" not in body["message"]
    assert "simulated report service outage" not in body["message"]
