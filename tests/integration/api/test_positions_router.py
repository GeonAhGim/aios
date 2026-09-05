"""LB-19 통합테스트 — /v1/positions 읽기 라우터. 실제 FastAPI 앱 + 실제
테스트 DB(TEST_DATABASE_URL → conftest가 DATABASE_URL로 옮김).

쓰기 API가 없으므로 선행 상태(pos_account/pos_snapshot/pos_journal/
pos_nav_daily)는 LB-9 어댑터를 직접 호출해 만든다 — 라우터가 우회할 수 있는
쓰기 경로가 HTTP에 없다는 사실 자체가 검증 대상이다(§9 LB-19 "쓰기 없음").
교차 테넌트 검사는 응답 상태코드뿐 아니라 봉투의 error_code·키 집합까지
미존재 응답과 같은지(동형) 비교한다."""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.data.models.base import Currency, Money
from src.foundation.positions.adapters.postgres_journal_repository import (
    PostgresJournalRepository,
)
from src.foundation.positions.adapters.postgres_nav_repository import PostgresNavRepository
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.contracts.v1 import (
    CostMethod,
    JournalEntryType,
    NAVSnapshot,
    PositionSnapshotView,
)
from src.main import app
from tests.conftest import lifespan_context_with_retry, retry_too_many_connections

STRONG_PASSWORD = "Str0ng!Passw0rd"
BASE = "/v1/positions"


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await retry_too_many_connections(
        lambda: asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    )
    yield p
    await p.close()


@pytest.fixture
async def client():
    async with lifespan_context_with_retry(app):
        # raise_app_exceptions=False — 도메인 예외는 전역 핸들러가 봉투로 번역하고
        # Starlette가 정상 응답 뒤에도 재전파하므로(test_auth_router.py 근거).
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


async def _register(client: AsyncClient) -> tuple[dict, UUID]:
    response = await client.post(
        "/auth/register",
        json={"email": f"test-{uuid.uuid4().hex}@example.com", "password": STRONG_PASSWORD},
    )
    headers = {"Authorization": f"Bearer {response.json()['data']['access_token']}"}
    me = await client.get("/users/me", headers=headers)
    return headers, UUID(me.json()["data"]["user_id"])


async def _create_account(pool: asyncpg.Pool, tenant_id: UUID) -> UUID:
    async with pool.acquire() as conn:
        account_id: UUID = await conn.fetchval(
            "INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method) "
            "VALUES ($1, $2, $3, $4) RETURNING account_id",
            tenant_id,
            f"V{uuid.uuid4().hex[:8]}",
            Currency.KRW.value,
            CostMethod.FIFO.value,
        )
    return account_id


async def _open_position(
    pool: asyncpg.Pool, *, tenant_id: UUID, account_id: UUID, quantity: Decimal
) -> PositionSnapshotView:
    key = f"TESTVENUE:{uuid.uuid4().hex}:strat:exec"
    snapshot = PositionSnapshotView(
        position_key=key,
        tenant_id=tenant_id,
        account_id=account_id,
        instrument_id=uuid.uuid4(),
        quantity=quantity,
        avg_cost=Money(amount=Decimal("100"), currency=Currency.KRW),
        cost_method=CostMethod.FIFO,
        lots=[],
        realized_pnl_base=Decimal("0"),
        unrealized_pnl_base=None,
        fees_base=Decimal("0"),
        funding_base=Decimal("0"),
        mark_price=None,
        mark_at=None,
        base_currency=Currency.KRW,
        last_journal_seq=0,
        updated_at=datetime.now(timezone.utc),
    )
    repo = PostgresSnapshotRepository(pool)
    async with pool.acquire() as conn, conn.transaction():
        return await repo.upsert(conn, snapshot, expected_seq=0)


async def _append_fills(pool: asyncpg.Pool, position_key: str, count: int) -> None:
    repo = PostgresJournalRepository(pool)
    for i in range(count):
        async with pool.acquire() as conn, conn.transaction():
            await repo.append(
                conn,
                position_key=position_key,
                entry_type=JournalEntryType.FILL,
                qty_delta=Decimal("1"),
                price=Money(amount=Decimal("100"), currency=Currency.KRW),
                fee=None,
                realized_pnl_base=Decimal("0"),
                fx_rate=None,
                fx_source=None,
                source_event_type="fill",
                source_event_id=f"{position_key}:{i}",
                idempotency_key=f"fill:{position_key}:{i}",
                occurred_at=datetime.now(timezone.utc),
            )


async def _insert_nav(pool: asyncpg.Pool, account_id: UUID, day: date, cash: Decimal) -> None:
    nav = NAVSnapshot(
        account_id=account_id,
        nav_date=day,
        base_currency=Currency.KRW,
        opening_nav=cash,
        cash=cash,
        positions_mv=Decimal("0"),
        realized=Decimal("0"),
        unrealized_delta=Decimal("0"),
        funding=Decimal("0"),
        fees=Decimal("0"),
        flows=Decimal("0"),
        closing_nav=cash,
        fx_rates=[],
        source_hash="ab" * 32,
    )
    async with pool.acquire() as conn:
        await PostgresNavRepository(pool).insert(conn, nav)


def _nav_params(account_id: UUID, start: str, end: str) -> dict[str, str]:
    return {"account_id": str(account_id), "start_date": start, "end_date": end}


def _assert_error_envelope(body: dict, code: str) -> None:
    assert body["error_code"] == code
    assert set(body) >= {"error_code", "message", "trace_id"}
    assert "data" not in body


# --- GET /positions -------------------------------------------------------


async def test_positions_require_authentication(client):
    response = await client.get(BASE)
    assert response.status_code == 401


async def test_list_positions_returns_own_open_positions_and_filters(client, pool):
    headers, tenant_id = await _register(client)
    account_id = await _create_account(pool, tenant_id)
    opened = await _open_position(
        pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("2")
    )
    await _open_position(pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1"))
    await _open_position(pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("0"))

    response = await client.get(BASE, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {"data", "meta"}
    keys = {item["position_key"] for item in body["data"]["items"]}
    assert len(keys) == 2 and opened.position_key in keys

    filtered = await client.get(
        BASE,
        headers=headers,
        params={"account_id": str(account_id), "instrument_id": str(opened.instrument_id)},
    )
    items = filtered.json()["data"]["items"]
    assert [item["position_key"] for item in items] == [opened.position_key]
    # Decimal은 문자열로 온다(NUMERIC(30,10) 스케일 그대로) — Number 변환 금지(§3.4)
    assert isinstance(items[0]["quantity"], str)
    assert Decimal(items[0]["quantity"]) == Decimal("2")
    assert items[0]["unrealized_pnl_base"] is None  # 마크 없으면 0이 아니라 null
    assert items[0]["schema_version"] == "v1"


async def test_list_positions_other_tenant_account_is_404_isomorphic(client, pool):
    victim_headers, victim_id = await _register(client)
    attacker_headers, _ = await _register(client)
    account_id = await _create_account(pool, victim_id)
    await _open_position(pool, tenant_id=victim_id, account_id=account_id, quantity=Decimal("1"))

    cross = await client.get(
        BASE, headers=attacker_headers, params={"account_id": str(account_id)}
    )
    ghost = await client.get(
        BASE, headers=attacker_headers, params={"account_id": str(uuid.uuid4())}
    )
    assert cross.status_code == ghost.status_code == 404
    _assert_error_envelope(cross.json(), "RESOURCE_NOT_FOUND")
    assert set(cross.json()) == set(ghost.json())

    own = await client.get(BASE, headers=victim_headers, params={"account_id": str(account_id)})
    assert own.status_code == 200 and len(own.json()["data"]["items"]) == 1


# --- GET /positions/{key}/journal ------------------------------------------


async def test_journal_cursor_pagination_walks_in_sequence_order(client, pool):
    headers, tenant_id = await _register(client)
    account_id = await _create_account(pool, tenant_id)
    opened = await _open_position(
        pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1")
    )
    await _append_fills(pool, opened.position_key, 3)

    first = await client.get(
        f"{BASE}/{opened.position_key}/journal", headers=headers, params={"limit": 2}
    )
    assert first.status_code == 200
    body = first.json()
    assert [e["sequence_no"] for e in body["data"]["items"]] == [1, 2]
    assert body["meta"]["page"]["next_cursor"] == "2"
    assert body["data"]["items"][0]["entry_type"] == "FILL"
    assert body["data"]["items"][0]["prev_hash"] is None

    second = await client.get(
        f"{BASE}/{opened.position_key}/journal",
        headers=headers,
        params={"limit": 2, "cursor": body["meta"]["page"]["next_cursor"]},
    )
    body2 = second.json()
    assert [e["sequence_no"] for e in body2["data"]["items"]] == [3]
    assert body2["meta"]["page"]["next_cursor"] is None


async def test_journal_cross_tenant_is_404_isomorphic_with_unknown_key(client, pool):
    _, victim_id = await _register(client)
    attacker_headers, _ = await _register(client)
    account_id = await _create_account(pool, victim_id)
    opened = await _open_position(
        pool, tenant_id=victim_id, account_id=account_id, quantity=Decimal("1")
    )
    await _append_fills(pool, opened.position_key, 1)

    cross = await client.get(
        f"{BASE}/{opened.position_key}/journal", headers=attacker_headers
    )
    ghost = await client.get(
        f"{BASE}/TESTVENUE:nope:strat:exec/journal", headers=attacker_headers
    )
    assert cross.status_code == ghost.status_code == 404
    _assert_error_envelope(cross.json(), "RESOURCE_NOT_FOUND")
    assert cross.json()["error_code"] == ghost.json()["error_code"]


async def test_journal_rejects_malformed_cursor(client, pool):
    headers, tenant_id = await _register(client)
    account_id = await _create_account(pool, tenant_id)
    opened = await _open_position(
        pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1")
    )

    response = await client.get(
        f"{BASE}/{opened.position_key}/journal", headers=headers, params={"cursor": "abc"}
    )
    assert response.status_code == 400
    _assert_error_envelope(response.json(), "VALIDATION_INVALID_FIELD")


# --- GET /positions/nav ------------------------------------------------------


async def test_nav_series_is_ascending_and_exposes_missing_days(client, pool):
    headers, tenant_id = await _register(client)
    account_id = await _create_account(pool, tenant_id)
    await _insert_nav(pool, account_id, date(2026, 9, 3), Decimal("1000"))
    await _insert_nav(pool, account_id, date(2026, 9, 1), Decimal("900"))

    response = await client.get(
        f"{BASE}/nav",
        headers=headers,
        params=_nav_params(account_id, "2026-09-01", "2026-09-03"),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert [item["nav_date"] for item in data["items"]] == ["2026-09-01", "2026-09-03"]
    assert Decimal(data["items"][0]["closing_nav"]) == Decimal("900")
    assert data["missing_dates"] == ["2026-09-02"]


async def test_nav_cross_tenant_is_404_and_bad_range_is_rejected(client, pool):
    _, victim_id = await _register(client)
    attacker_headers, attacker_id = await _register(client)
    victim_account = await _create_account(pool, victim_id)
    await _insert_nav(pool, victim_account, date(2026, 9, 1), Decimal("1"))
    own_account = await _create_account(pool, attacker_id)

    cross = await client.get(
        f"{BASE}/nav",
        headers=attacker_headers,
        params=_nav_params(victim_account, "2026-09-01", "2026-09-01"),
    )
    assert cross.status_code == 404
    _assert_error_envelope(cross.json(), "RESOURCE_NOT_FOUND")

    reversed_range = await client.get(
        f"{BASE}/nav",
        headers=attacker_headers,
        params=_nav_params(own_account, "2026-09-02", "2026-09-01"),
    )
    assert reversed_range.status_code == 400
    _assert_error_envelope(reversed_range.json(), "VALIDATION_INVALID_FIELD")

    too_long = await client.get(
        f"{BASE}/nav",
        headers=attacker_headers,
        params=_nav_params(own_account, "2025-01-01", "2026-09-01"),
    )
    assert too_long.status_code == 400
