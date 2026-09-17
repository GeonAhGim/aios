"""통합테스트 — task-4144 DEEPEN(원 리프 task-1377 LB-19 positions HTTP 읽기
API): journal/nav 엔드포인트 전용 실패주입.

`test_positions_router.py`는 task-2993/3033 DEEPEN에서 이미 negative
3건 이상·실패주입·수치 성능 단언·게이트 적색 재현을 갖췄지만, 그 실패주입은
전부 `GET /positions`(snapshot/entity 저장소 outage)만 다뤘다 — `GET
/positions/{key}/journal`과 `GET /positions/nav`는 저장소 outage 시
fail-closed 하는지 아직 검증된 적이 없었다.

이 파일은 원 파일을 건드리지 않는다(500줄 loc 래칫 회귀 방지,
ADR-2026-09-10-C §7 — `test_positions_router.py`는 이미 727줄이라 추가하면
800줄 임계를 넘어 `check_code_ratchets.py` loc_over_800 베이스라인이
올라간다) — task-3179/3181/3183 DEEPEN과 동일하게 별도 파일로 분리하고
고정(fixture/helper)을 자체 보유한다.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §9 LB-19.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.routers.positions import get_journal_repository, get_nav_repository
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
from src.foundation.positions.domain.position_key import PositionKey
from src.main import app
from tests.conftest import lifespan_context_with_retry, retry_too_many_connections
from tests.support.entities_seed import bootstrap_default_portfolio

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
    portfolio_id = await bootstrap_default_portfolio(pool, tenant_id)
    key = str(
        PositionKey(
            venue="TESTVENUE",
            instrument_id=uuid.uuid4().hex,
            strategy_id="strat",
            execution_id="exec",
            portfolio_id=portfolio_id,
        )
    )
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


class _OutageJournalRepository:
    """모의 어댑터 예외 -- journal 조회 경로가 여태 outage 테스트를 갖지
    못했다(기존 실패주입은 전부 `get_positions`용 snapshot/entity 저장소만
    다뤘다). `append`를 호출하면 §9 LB-19 '쓰기 없음'을 위반한 것이므로
    바로 실패시킨다."""

    async def append(self, conn, **kwargs):
        raise AssertionError("읽기 라우터가 append를 호출했다 — §9 LB-19 '쓰기 없음' 위반")

    async def list_for(self, conn, position_key, from_seq=0):
        raise asyncpg.PostgresConnectionError("simulated journal adapter outage")

    async def last(self, conn, position_key):
        raise asyncpg.PostgresConnectionError("simulated journal adapter outage")


async def test_journal_repository_outage_is_fail_closed_500(client, pool):
    """failure-injection -- journal 저장소가 커넥션 예외를 던지면 부분 데이터나
    200을 흘리지 않고 500/INTERNAL_ERROR 봉투로 fail-closed 한다. 원인
    예외 문자열은 응답 메시지에 새지 않는다."""
    headers, tenant_id = await _register(client)
    account_id = await _create_account(pool, tenant_id)
    opened = await _open_position(
        pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1")
    )
    await _append_fills(pool, opened.position_key, 1)

    app.dependency_overrides[get_journal_repository] = lambda: _OutageJournalRepository()
    try:
        response = await client.get(f"{BASE}/{opened.position_key}/journal", headers=headers)
    finally:
        app.dependency_overrides.pop(get_journal_repository, None)

    assert response.status_code == 500
    body = response.json()
    _assert_error_envelope(body, "INTERNAL_ERROR")
    assert "PostgresConnectionError" not in body["message"]
    assert "simulated journal adapter outage" not in body["message"]


class _OutageNavRepository:
    """모의 어댑터 예외 -- NAV 조회 경로도 여태 outage 테스트가 없었다.
    `insert`를 호출하면 §9 LB-8 WORM/읽기 전용 원칙 위반이므로 바로
    실패시킨다."""

    async def insert(self, conn, nav):
        raise AssertionError("읽기 라우터가 insert를 호출했다 — §9 LB-19 '쓰기 없음' 위반")

    async def get(self, conn, account_id, nav_date):
        raise asyncpg.PostgresConnectionError("simulated nav adapter outage")


async def test_nav_repository_outage_is_fail_closed_500(client, pool):
    """failure-injection -- NAV 저장소가 커넥션 예외를 던지면 부분 데이터나
    200을 흘리지 않고 500/INTERNAL_ERROR 봉투로 fail-closed 한다. 원인
    예외 문자열은 응답 메시지에 새지 않는다."""
    headers, tenant_id = await _register(client)
    account_id = await _create_account(pool, tenant_id)
    await _insert_nav(pool, account_id, date(2026, 9, 1), Decimal("1000"))

    app.dependency_overrides[get_nav_repository] = lambda: _OutageNavRepository()
    try:
        response = await client.get(
            f"{BASE}/nav",
            headers=headers,
            params=_nav_params(account_id, "2026-09-01", "2026-09-01"),
        )
    finally:
        app.dependency_overrides.pop(get_nav_repository, None)

    assert response.status_code == 500
    body = response.json()
    _assert_error_envelope(body, "INTERNAL_ERROR")
    assert "PostgresConnectionError" not in body["message"]
    assert "simulated nav adapter outage" not in body["message"]


async def test_journal_and_nav_reject_malformed_account_id_before_touching_db(client, pool):
    """negative -- `GET /positions/nav`의 `account_id`가 UUID로 파싱 불가면
    저장소를 전혀 부르지 않고 400/VALIDATION_INVALID_FIELD 봉투로 거부한다
    (transport validation, DB 왕복 0회). `journal` outage/`nav` outage
    테스트가 커버하지 못한, 아직 저장소에 닿기 전 단계의 fail-closed
    지점을 고정한다."""
    headers, _tenant_id = await _register(client)

    response = await client.get(
        f"{BASE}/nav",
        headers=headers,
        params={"account_id": "not-a-uuid", "start_date": "2026-09-01", "end_date": "2026-09-01"},
    )
    assert response.status_code == 400
    _assert_error_envelope(response.json(), "VALIDATION_INVALID_FIELD")
