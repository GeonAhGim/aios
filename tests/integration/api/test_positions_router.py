"""LB-19 / FA-6: position listing and tenant/portfolio scope."""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from uuid import UUID

import pytest

from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from tests.integration.foundation.entities.conftest import build_hierarchy
from tests.support.perf_budget import PerfBudget
from tests.support.positions_router import (
    BASE,
    _assert_error_envelope,
    _create_account,
    _open_position,
    _register,
)
from tests.support.positions_router import client as client
from tests.support.positions_router import pool as pool


async def test_list_positions_other_tenant_account_is_404_isomorphic(client, pool):
    victim_headers, victim_id = await _register(client)
    attacker_headers, _ = await _register(client)
    account_id = await _create_account(pool, victim_id)
    await _open_position(pool, tenant_id=victim_id, account_id=account_id, quantity=Decimal("1"))

    cross = await client.get(BASE, headers=attacker_headers, params={"account_id": str(account_id)})
    ghost = await client.get(
        BASE, headers=attacker_headers, params={"account_id": str(uuid.uuid4())}
    )
    assert cross.status_code == ghost.status_code == 404
    _assert_error_envelope(cross.json(), "RESOURCE_NOT_FOUND")
    assert set(cross.json()) == set(ghost.json())

    own = await client.get(BASE, headers=victim_headers, params={"account_id": str(account_id)})
    assert own.status_code == 200 and len(own.json()["data"]["items"]) == 1


async def test_list_positions_portfolio_id_filters_to_that_portfolio_only(client, pool):
    headers, tenant_id = await _register(client)
    repo = PostgresEntityRepository(pool)
    hierarchy_a = await build_hierarchy(pool, repo, tenant_id=tenant_id)
    hierarchy_b = await build_hierarchy(pool, repo, tenant_id=tenant_id)
    account_id = await _create_account(pool, tenant_id)
    in_a = await _open_position(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        quantity=Decimal("1"),
        portfolio_id=hierarchy_a.portfolio.portfolio_id,
    )
    await _open_position(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        quantity=Decimal("1"),
        portfolio_id=hierarchy_b.portfolio.portfolio_id,
    )

    response = await client.get(
        BASE, headers=headers, params={"portfolio_id": str(hierarchy_a.portfolio.portfolio_id)}
    )
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert [item["position_key"] for item in items] == [in_a.position_key]


async def test_list_positions_portfolio_id_rejects_cross_tenant_scope_fail_closed(client, pool):
    """negative — 다른 테넌트 소유 portfolio_id를 주면 그 tenant의 포지션
    전체를 돌려주는 대신(전체 반환 폴백 금지) 404로 거부한다."""
    _, victim_id = await _register(client)
    attacker_headers, attacker_id = await _register(client)
    repo = PostgresEntityRepository(pool)
    victim_hierarchy = await build_hierarchy(pool, repo, tenant_id=victim_id)
    attacker_account = await _create_account(pool, attacker_id)
    await _open_position(
        pool, tenant_id=attacker_id, account_id=attacker_account, quantity=Decimal("1")
    )

    response = await client.get(
        BASE,
        headers=attacker_headers,
        params={"portfolio_id": str(victim_hierarchy.portfolio.portfolio_id)},
    )
    assert response.status_code == 404
    _assert_error_envelope(response.json(), "RESOURCE_NOT_FOUND")


async def test_list_positions_portfolio_id_rejects_unknown_portfolio(client, pool):
    headers, tenant_id = await _register(client)
    account_id = await _create_account(pool, tenant_id)
    await _open_position(pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1"))

    response = await client.get(BASE, headers=headers, params={"portfolio_id": str(uuid.uuid4())})
    assert response.status_code == 404
    _assert_error_envelope(response.json(), "RESOURCE_NOT_FOUND")


async def test_list_positions_closed_portfolio_scope_is_rejected_fail_closed(client, pool):
    """게이트 적색 재현 -- portfolio_id는 실존하고 이 tenant 소유지만 폐쇄
    (`closed_at` NOT NULL)된 실제 DB row다(목이 아니다). `resolve_portfolio_scope`의
    `_require_open` 검사가 배선에서 빠지면 이 테스트는 200과 함께 폐쇄
    포트폴리오의 포지션을 그대로 흘려 적색이 된다 -- 존재+소유 확인만으로는
    부족하고 개방 상태까지 fail-closed로 확인해야 함을 실 DB 상태로 증명한다."""
    headers, tenant_id = await _register(client)
    repo = PostgresEntityRepository(pool)
    hierarchy = await build_hierarchy(pool, repo, tenant_id=tenant_id)
    account_id = await _create_account(pool, tenant_id)
    await _open_position(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        quantity=Decimal("1"),
        portfolio_id=hierarchy.portfolio.portfolio_id,
    )
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE portfolio SET closed_at = now() WHERE portfolio_id = $1",
            hierarchy.portfolio.portfolio_id,
        )

    response = await client.get(
        BASE, headers=headers, params={"portfolio_id": str(hierarchy.portfolio.portfolio_id)}
    )
    assert response.status_code == 404
    _assert_error_envelope(response.json(), "RESOURCE_NOT_FOUND")


async def test_list_positions_portfolio_id_concurrent_mixed_tenants_do_not_cross_leak(client, pool):
    """D3증거 -- 서로 다른 tenant가 `portfolio_id`로 스코프한 `GET /positions`를
    asyncio.gather로 동시에 섞어 호출해도(공유 커넥션 풀·앱 인스턴스) 각
    요청은 자신의 tenant_id/portfolio_id 기준으로만 결과를 받는다 -- 동시
    실행이 만드는 경합으로 한 tenant의 포지션이 다른 tenant 응답에 섞여
    드는 사고(교차 유출)가 없음을 증명한다."""
    repo = PostgresEntityRepository(pool)
    sessions: list[tuple[dict, UUID]] = []
    for _ in range(3):
        headers, tenant_id = await _register(client)
        hierarchy = await build_hierarchy(pool, repo, tenant_id=tenant_id)
        account_id = await _create_account(pool, tenant_id)
        await _open_position(
            pool,
            tenant_id=tenant_id,
            account_id=account_id,
            quantity=Decimal("1"),
            portfolio_id=hierarchy.portfolio.portfolio_id,
        )
        sessions.append((headers, hierarchy.portfolio.portfolio_id))

    async def _fetch(headers: dict, portfolio_id: UUID):
        return await client.get(BASE, headers=headers, params={"portfolio_id": str(portfolio_id)})

    calls = [_fetch(headers, portfolio_id) for headers, portfolio_id in sessions for _ in range(3)]
    responses = await asyncio.gather(*calls)

    for response in responses:
        assert response.status_code == 200
        assert len(response.json()["data"]["items"]) == 1


# ---------------------------------------------------------------------------
# Performance measurement — task-16078 (PERF-detection gate-red)
# ---------------------------------------------------------------------------

# LB-19: GET /v1/positions is a read-only DB query with tenant scoping.
# Budget is wall-clock latency for a single call returning ≤2 positions
# (calibrated against a typical dev-box with local Postgres).
_LIST_POSITIONS_LATENCY_BUDGET_MS = 200.0


async def _assert_list_positions_within_budget(
    client,
    pool,
    *,
    n: int,
    warmup: int,
) -> None:
    """LB-19: 단일 호출 지연이 예산 내에 있음을 측정한다 (n회 중 최소, warmup회는 버린다).
    PerfBudget.best_of_async 로 wall-clock 을 재고 budget_ms 단언으로
    정량 검증한다 (task-11539 감사 후속 — raw 타이머가 아닌 공용 헬퍼)."""
    headers, tenant_id = await _register(client)
    account_id = await _create_account(pool, tenant_id)
    await _open_position(pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1"))

    perf_budget = PerfBudget()

    async def _fetch() -> object:
        return await client.get(BASE, headers=headers)

    sample = await perf_budget.best_of_async(_fetch, n=n, warmup=warmup)
    assert sample.wall_ms < _LIST_POSITIONS_LATENCY_BUDGET_MS, (
        f"wall={sample.wall_ms:.3f}ms budget={_LIST_POSITIONS_LATENCY_BUDGET_MS}ms "
        f"cpu={sample.cpu_ms:.3f}ms"
    )


@pytest.mark.perf
async def test_list_positions_single_call_latency_within_budget(
    client,
    pool,
) -> None:
    """LB-19: normal 입력에서 단일 호출 지연이 예산 내에 있음을 측정한다.

    단발 1회 측정은 예산 근처에서 흔들렸다: full 직렬 perf에서 이 호출이 260ms로 나와 적색 재현
    테스트(대조군으로 이 측정을 다시 부른다)가 3회 연속 적색이었다(d91d3acde, cda523ac3, 715153399.
    예약 코어 단독 실행은 8/8 통과). 첫 요청의 워밍업을 흡수하고 3회 중 최소값을 쓴다.
    예산·단언은 그대로다."""
    await _assert_list_positions_within_budget(client, pool, n=3, warmup=1)


@pytest.mark.perf
async def test_gate_red_reproduction_positions_latency_budget_catches_db_regression(
    monkeypatch: pytest.MonkeyPatch,
    client,
    pool,
) -> None:
    """게이트 적색 재현 — 정상 호출이 녹색인 것과 동일 단언을 10배 sleep
    주입에서 적색으로 만든다 (task-11539 감사: 이 파일에 gate-red 테스트가
    없었음)."""
    # 먼저 제어 테스트: 정상 입력에서 녹색 확인
    await _assert_list_positions_within_budget(client, pool, n=3, warmup=1)

    # sleep 을 monkeypatch 로 주입 — async 클라이언트 내부 await 지점에
    # 지연을 더해 전체 지연이 예산을 초과하도록 만든다.
    original_request = client.request

    async def _delayed_request(*args, **kwargs):
        await asyncio.sleep(_LIST_POSITIONS_LATENCY_BUDGET_MS * 10 / 1000)
        return await original_request(*args, **kwargs)

    monkeypatch.setattr(client, "request", _delayed_request)

    # 동일한 단언이 지연 주입에서 실패해야 한다. 주입 구간은 요청 1번(2초 sleep)만 잰다.
    # 4번(8초)으로 늘리면 백그라운드 루프가 서킷브레이커를 halted로 남겨
    # 다음 테스트 teardown 센티널이 적색이 된다(측정으로 확인).
    with pytest.raises(AssertionError) as exc_info:
        await _assert_list_positions_within_budget(client, pool, n=1, warmup=0)
    # 실제 budget 단언 실패인지 확인 (다른 AssertionError 가 아님)
    assert "wall=" in str(exc_info.value) or "budget=" in str(exc_info.value)
