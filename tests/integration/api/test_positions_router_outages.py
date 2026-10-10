"""LB-19 / FA-6: repository outages fail closed."""

from __future__ import annotations

from decimal import Decimal

import asyncpg

from src.api.routers.positions import get_entity_repository, get_snapshot_repository
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.main import app
from tests.integration.foundation.entities.conftest import build_hierarchy
from tests.support.positions_router import (
    BASE,
    _assert_error_envelope,
    _create_account,
    _open_position,
    _register,
)
from tests.support.positions_router import client as client
from tests.support.positions_router import pool as pool


class _OutageSnapshotRepository:
    """모의 어댑터 예외 -- 커넥션 단절 등 인프라 장애를 흉내낸다. 도메인
    예외(PositionNotFoundError 등)가 아니라 asyncpg 드라이버 예외라 전역
    `Exception` 핸들러의 미분류(INTERNAL_ERROR) 경로를 탄다."""

    async def get(self, conn, tenant_id, position_key):
        raise asyncpg.PostgresConnectionError("simulated adapter outage")

    async def upsert(self, conn, snapshot, expected_seq):
        raise AssertionError("읽기 라우터가 upsert를 호출했다 — §9 LB-19 '쓰기 없음' 위반")

    async def list_open(self, conn, tenant_id, account_id):
        raise asyncpg.PostgresConnectionError("simulated adapter outage")



async def test_list_positions_snapshot_adapter_outage_is_fail_closed_500(client, pool):
    """failure-injection -- SnapshotRepository 어댑터가 커넥션 예외를 던지면
    부분 데이터나 200을 흘리지 않고 500/INTERNAL_ERROR 봉투로 fail-closed
    한다. 원인 예외 문자열은 로그에만 남고 응답 메시지에는 새지 않는다
    (handlers.py `_handle_domain_or_unknown_exception`)."""
    headers, tenant_id = await _register(client)
    account_id = await _create_account(pool, tenant_id)
    await _open_position(pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1"))

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



class _OutageEntityRepository:
    """FA-6 실패주입 -- entities 저장소가 `resolve_portfolio_scope` 조회
    도중 커넥션 예외를 던지는 인프라 장애를 흉내낸다(도메인 예외가 아니라
    asyncpg 드라이버 예외). DEPTH 감사(task-2724)가 지적한 공백: 지금까지의
    `portfolio_id` negative는 전부 "존재하지 않음/폐쇄됨" 같은 정상 입력
    검증 거부였을 뿐, entities 저장소 자체가 죽는 경우는 흉내낸 적이 없었다."""

    async def get_legal_entity(self, tenant_id, entity_id):
        raise asyncpg.PostgresConnectionError("simulated entities adapter outage")

    async def get_fund(self, tenant_id, fund_id):
        raise asyncpg.PostgresConnectionError("simulated entities adapter outage")

    async def get_portfolio(self, tenant_id, portfolio_id):
        raise asyncpg.PostgresConnectionError("simulated entities adapter outage")

    async def get_sub_account(self, tenant_id, sub_account_id):
        raise asyncpg.PostgresConnectionError("simulated entities adapter outage")



async def test_list_positions_portfolio_id_entities_outage_is_fail_closed_500(client, pool):
    """failure-injection -- `portfolio_id` 스코프 검증에 쓰는 entities
    저장소가 커넥션 예외를 던지면, 이미 조회를 시작했다는 이유로 스코프
    없이(또는 unscoped) 200을 흘리지 않고 500/INTERNAL_ERROR 봉투로
    fail-closed 한다. 원인 예외 문자열은 응답 메시지에 새지 않는다."""
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

    app.dependency_overrides[get_entity_repository] = lambda: _OutageEntityRepository()
    try:
        response = await client.get(
            BASE, headers=headers, params={"portfolio_id": str(hierarchy.portfolio.portfolio_id)}
        )
    finally:
        app.dependency_overrides.pop(get_entity_repository, None)

    assert response.status_code == 500
    body = response.json()
    _assert_error_envelope(body, "INTERNAL_ERROR")
    assert "PostgresConnectionError" not in body["message"]
    assert "simulated entities adapter outage" not in body["message"]
