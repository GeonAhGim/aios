"""20번대 통합테스트 — /reports 라우터. 실제 FastAPI 앱 + 실제 dev DB."""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values
from httpx import ASGITransport, AsyncClient

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
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


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
