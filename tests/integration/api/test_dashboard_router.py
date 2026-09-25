"""U-2a 통합테스트 — `/v1/accounts/summary` 읽기 라우터. 실제 FastAPI 앱 +
실제 테스트 DB(TEST_DATABASE_URL → conftest가 DATABASE_URL로 옮김).

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md#U-2,
ADR-2026-09-09-B Decision C.

이번 리프는 스코프를 좁힌다(task-2629 decision, 3회차 턴 예산 초과 이후) —
포지션+현금 집계만 검증한다. 펀드/포트폴리오 다중 스코프·노출(exposure)은
후속 리프. `tests/integration/api/test_positions_router.py`(LB-19/FA-6)와
같은 관행을 재사용한다: 실 DB fixture로 선행 상태를 만들고, 라우터가 우회할
수 있는 쓰기 경로가 HTTP에 없다는 사실 자체가 검증 대상이다."""

from __future__ import annotations

import asyncio
import math
import os
import time
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.routers.dashboard import get_snapshot_repository
from src.data.models.base import Currency, Money
from src.foundation.positions.adapters.postgres_nav_repository import PostgresNavRepository
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.contracts.v1 import (
    CostMethod,
    NAVSnapshot,
    PositionSnapshotView,
)
from src.foundation.positions.domain.position_key import PositionKey
from src.main import app
from tests.conftest import lifespan_context_with_retry, retry_too_many_connections
from tests.support.entities_seed import bootstrap_default_portfolio

STRONG_PASSWORD = "Str0ng!Passw0rd"
BASE = "/v1/accounts/summary"


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


async def _create_account(
    pool: asyncpg.Pool, tenant_id: UUID, *, venue: str, currency: Currency = Currency.KRW
) -> UUID:
    async with pool.acquire() as conn:
        account_id: UUID = await conn.fetchval(
            "INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method) "
            "VALUES ($1, $2, $3, $4) RETURNING account_id",
            tenant_id,
            venue,
            currency.value,
            CostMethod.FIFO.value,
        )
    return account_id


async def _open_position(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
    account_id: UUID,
    quantity: Decimal,
    currency: Currency = Currency.KRW,
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
        avg_cost=Money(amount=Decimal("100"), currency=currency),
        cost_method=CostMethod.FIFO,
        lots=[],
        realized_pnl_base=Decimal("0"),
        unrealized_pnl_base=None,
        fees_base=Decimal("0"),
        funding_base=Decimal("0"),
        mark_price=None,
        mark_at=None,
        base_currency=currency,
        last_journal_seq=0,
        updated_at=datetime.now(timezone.utc),
    )
    repo = PostgresSnapshotRepository(pool)
    async with pool.acquire() as conn, conn.transaction():
        return await repo.upsert(conn, snapshot, expected_seq=0)


async def _insert_nav(
    pool: asyncpg.Pool,
    account_id: UUID,
    day: date,
    *,
    cash: Decimal,
    positions_mv: Decimal,
    currency: Currency = Currency.KRW,
) -> None:
    closing = cash + positions_mv
    nav = NAVSnapshot(
        account_id=account_id,
        nav_date=day,
        base_currency=currency,
        opening_nav=closing,
        cash=cash,
        positions_mv=positions_mv,
        realized=Decimal("0"),
        unrealized_delta=Decimal("0"),
        funding=Decimal("0"),
        fees=Decimal("0"),
        flows=Decimal("0"),
        closing_nav=closing,
        fx_rates=[],
        source_hash="ab" * 32,
    )
    async with pool.acquire() as conn:
        await PostgresNavRepository(pool).insert(conn, nav)


def _assert_error_envelope(body: dict, code: str) -> None:
    assert body["error_code"] == code
    assert set(body) >= {"error_code", "message", "trace_id"}
    assert "data" not in body


# --- GET /v1/accounts/summary -----------------------------------------------


async def test_accounts_summary_requires_authentication(client):
    response = await client.get(BASE)
    assert response.status_code == 401


async def test_accounts_summary_aggregates_kis_nh_bitget_accounts(client, pool):
    """DoD -- 3계좌(KIS paper·NH contract fixture·Bitget) 합산: 통화별 합계가
    개별 계좌 값의 정확한 Decimal 합과 일치한다(반올림 오차 0)."""
    headers, tenant_id = await _register(client)

    kis_account = await _create_account(pool, tenant_id, venue="KIS_PAPER", currency=Currency.KRW)
    await _open_position(pool, tenant_id=tenant_id, account_id=kis_account, quantity=Decimal("10"))
    await _insert_nav(
        pool, kis_account, date(2026, 9, 16), cash=Decimal("500000"), positions_mv=Decimal("120000")
    )

    nh_account = await _create_account(
        pool, tenant_id, venue="NH_CONTRACT_FIXTURE", currency=Currency.KRW
    )
    await _open_position(pool, tenant_id=tenant_id, account_id=nh_account, quantity=Decimal("5"))
    await _insert_nav(
        pool,
        nh_account,
        date(2026, 9, 16),
        cash=Decimal("250000.5"),
        positions_mv=Decimal("30000.25"),
    )

    bitget_account = await _create_account(pool, tenant_id, venue="BITGET", currency=Currency.USDT)
    await _open_position(
        pool,
        tenant_id=tenant_id,
        account_id=bitget_account,
        quantity=Decimal("2"),
        currency=Currency.USDT,
    )
    await _insert_nav(
        pool,
        bitget_account,
        date(2026, 9, 16),
        cash=Decimal("1000.123456"),
        positions_mv=Decimal("500.876544"),
        currency=Currency.USDT,
    )

    response = await client.get(BASE, headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["accounts"]) == 3
    assert {a["venue"] for a in data["accounts"]} == {"KIS_PAPER", "NH_CONTRACT_FIXTURE", "BITGET"}
    for account in data["accounts"]:
        assert len(account["open_positions"]) == 1
        # Decimal은 문자열로 온다 -- Number 변환 금지(§3.4)
        assert isinstance(account["cash"], str)

    totals_by_ccy = {t["base_currency"]: t for t in data["totals"]}
    assert set(totals_by_ccy) == {"KRW", "USDT"}
    assert Decimal(totals_by_ccy["KRW"]["cash"]) == Decimal("500000") + Decimal("250000.5")
    assert Decimal(totals_by_ccy["KRW"]["positions_mv"]) == Decimal("120000") + Decimal("30000.25")
    assert Decimal(totals_by_ccy["USDT"]["cash"]) == Decimal("1000.123456")
    assert Decimal(totals_by_ccy["USDT"]["positions_mv"]) == Decimal("500.876544")


async def test_accounts_summary_account_id_filters_to_that_account_only(client, pool):
    headers, tenant_id = await _register(client)
    kis_account = await _create_account(pool, tenant_id, venue="KIS_PAPER")
    await _create_account(pool, tenant_id, venue="BITGET", currency=Currency.USDT)

    response = await client.get(BASE, headers=headers, params={"account_id": str(kis_account)})
    assert response.status_code == 200
    data = response.json()["data"]
    assert [a["account_id"] for a in data["accounts"]] == [str(kis_account)]


async def test_accounts_summary_without_nav_returns_none_not_zero(client, pool):
    """cash/positions_mv/closing_nav는 EOD 정산이 아직 안 됐으면 0이 아니라
    None -- "정산 미완료"와 "실제 잔고 0"을 섞지 않는다(모듈 docstring)."""
    headers, tenant_id = await _register(client)
    await _create_account(pool, tenant_id, venue="KIS_PAPER")

    response = await client.get(BASE, headers=headers)
    assert response.status_code == 200
    account = response.json()["data"]["accounts"][0]
    assert account["cash"] is None
    assert account["positions_mv"] is None
    assert account["closing_nav"] is None
    assert account["nav_date"] is None
    assert response.json()["data"]["totals"] == []


async def test_accounts_summary_other_tenant_account_is_404_isomorphic(client, pool):
    victim_headers, victim_id = await _register(client)
    attacker_headers, _ = await _register(client)
    account_id = await _create_account(pool, victim_id, venue="KIS_PAPER")

    cross = await client.get(BASE, headers=attacker_headers, params={"account_id": str(account_id)})
    ghost = await client.get(
        BASE, headers=attacker_headers, params={"account_id": str(uuid.uuid4())}
    )
    assert cross.status_code == ghost.status_code == 404
    _assert_error_envelope(cross.json(), "RESOURCE_NOT_FOUND")
    assert set(cross.json()) == set(ghost.json())

    own = await client.get(BASE, headers=victim_headers, params={"account_id": str(account_id)})
    assert own.status_code == 200 and len(own.json()["data"]["accounts"]) == 1


class _OutageSnapshotRepository:
    """모의 어댑터 예외 -- 인프라 장애(커넥션 단절)를 흉내낸다. 도메인 예외가
    아니라 asyncpg 드라이버 예외라 전역 `Exception` 핸들러의 미분류
    (INTERNAL_ERROR) 경로를 탄다(test_positions_router.py와 동일 기법)."""

    async def get(self, conn, tenant_id, position_key):
        raise asyncpg.PostgresConnectionError("simulated adapter outage")

    async def upsert(self, conn, snapshot, expected_seq):
        raise AssertionError("읽기 라우터가 upsert를 호출했다 -- 쓰기 없음 위반")

    async def list_open(self, conn, tenant_id, account_id):
        raise asyncpg.PostgresConnectionError("simulated adapter outage")


async def test_accounts_summary_snapshot_adapter_outage_is_fail_closed_500(client, pool):
    """failure-injection -- SnapshotRepository 어댑터가 커넥션 예외를 던지면
    부분 데이터나 200을 흘리지 않고 500/INTERNAL_ERROR 봉투로 fail-closed
    한다. 원인 예외 문자열은 응답 메시지에 새지 않는다."""
    headers, tenant_id = await _register(client)
    await _create_account(pool, tenant_id, venue="KIS_PAPER")

    app.dependency_overrides[get_snapshot_repository] = lambda: _OutageSnapshotRepository()
    try:
        response = await client.get(BASE, headers=headers)
    finally:
        app.dependency_overrides.pop(get_snapshot_repository, None)

    assert response.status_code == 500
    body = response.json()
    _assert_error_envelope(body, "INTERNAL_ERROR")
    assert "PostgresConnectionError" not in body["message"]
    assert "simulated adapter outage" not in body["message"]


async def test_accounts_summary_concurrent_mixed_tenants_do_not_cross_leak(client, pool):
    """게이트 적색 재현/D3 -- 서로 다른 tenant가 `GET /v1/accounts/summary`를
    asyncio.gather로 동시에 섞어 호출해도(공유 커넥션 풀·앱 인스턴스) 각
    요청은 자신의 tenant_id 기준으로만 계좌를 받는다 -- `_owned_accounts`의
    `tenant_id` 필터가 빠지면 이 테스트는 동시성 경합 속에서 다른 tenant의
    계좌가 섞여 드는 교차 유출로 적색이 된다(LB-19 동일 기법)."""
    sessions: list[tuple[dict, UUID]] = []
    for _ in range(3):
        headers, tenant_id = await _register(client)
        account_id = await _create_account(pool, tenant_id, venue="KIS_PAPER")
        await _open_position(
            pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1")
        )
        sessions.append((headers, account_id))

    async def _fetch(headers: dict):
        return await client.get(BASE, headers=headers)

    calls = [_fetch(headers) for headers, _ in sessions for _ in range(3)]
    responses = await asyncio.gather(*calls)

    expected = [account_id for _, account_id in sessions for _ in range(3)]
    for account_id, response in zip(expected, responses, strict=True):
        assert response.status_code == 200
        accounts = response.json()["data"]["accounts"]
        assert [a["account_id"] for a in accounts] == [str(account_id)]


@pytest.mark.perf
async def test_accounts_summary_p95_latency_stays_within_normalized_ceiling(client, pool):
    """수치 성능 단언 -- 공유 TEST_DATABASE_URL의 절대 지연 변동성 때문에
    절대 ms 임계 대신, 가벼운 baseline 호출 1건 대비 정규화한 상한만
    게이트로 쓴다(test_positions_router.py p95 테스트와 동일 결정, DoD
    "응답 p95 300ms"는 운영 배포 기준이고 공유 로컬 DB에서는 baseline 대비
    배율로 회귀만 감시한다)."""
    headers, tenant_id = await _register(client)
    account_id = await _create_account(pool, tenant_id, venue="KIS_PAPER")
    await _open_position(pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1"))
    await _insert_nav(
        pool, account_id, date(2026, 9, 16), cash=Decimal("1000"), positions_mv=Decimal("100")
    )

    async def _call() -> float:
        started = time.monotonic()
        response = await client.get(BASE, headers=headers)
        elapsed = time.monotonic() - started
        assert response.status_code == 200
        return elapsed

    baseline_elapsed = await _call()
    samples = sorted([await _call() for _ in range(20)])
    p95 = samples[math.ceil(0.95 * len(samples)) - 1]

    ceiling = baseline_elapsed * 5 + 0.05
    assert p95 <= ceiling, (
        f"GET /v1/accounts/summary p95 지연 {p95:.4f}s가 정규화 상한 "
        f"{ceiling:.4f}s(baseline {baseline_elapsed:.4f}s)를 초과했습니다"
    )
