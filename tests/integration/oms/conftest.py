"""L4-06 `orders` 트리거 통합테스트 공용 픽스처.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-06.

`orders`의 FK 대상(`users`)만 최소로 채운다 — `execution_id`/`risk_decision_id`
등은 전부 NULL 허용이라 이 트리거 테스트에는 필요 없다(tests/adversarial/
risk/conftest.py의 `_insert_raw`와 같은 관례, 그쪽보다 훨씬 좁은 범위).
"""
from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.foundation.entities.contracts.v1 import EntityContext
from tests.integration.conftest import create_test_tenant, create_test_user
from tests.integration.foundation.entities.conftest import build_hierarchy

__all__ = [
    "create_test_user",
    "create_test_tenant",
    "insert_order",
    "insert_event",
    "arm_cutover_sql",
    "seed_entity_context",
]


async def seed_entity_context(pool: asyncpg.Pool, tenant_id: UUID) -> EntityContext:
    """FA-5 — `submit_order`가 요구하는 `entity_context`를 실제 4단 계층을
    영속화해 만든다(FA-2 `build_hierarchy` 재사용, 새 시딩 규칙 없음).
    `tenant_id`는 `create_test_tenant()`로 만든 id를 넘겨야 한다 — `legal_entity.
    tenant_id`가 `tenant(id)`를 FK하므로 `create_test_user()`만으로 만든
    id(대응 `tenant` 행 없음)를 넘기면 FK 위반으로 실패한다."""
    repo = PostgresEntityRepository(pool)
    hierarchy = await build_hierarchy(pool, repo, tenant_id=tenant_id)
    return EntityContext(
        tenant_id=tenant_id,
        legal_entity_id=hierarchy.legal_entity.entity_id,
        fund_id=hierarchy.fund.fund_id,
        portfolio_id=hierarchy.portfolio.portfolio_id,
        sub_account_id=hierarchy.sub_account.sub_account_id,
    )


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


# 단조(105번 조건부 UPDATE) — 이미 무장됐으면 no-op. 호출부가 항상 트랜잭션
# 안에서 실행하고 끝에 롤백해 공유 테스트 DB의 다른 테스트에 영향 없게 한다
# (tests/adversarial/risk/test_fence_race.py와 동일 관례).
arm_cutover_sql = (
    "UPDATE oms_order_transition_cutover SET cutover_at = now(), armed_by = 'test' "
    "WHERE id = 1 AND cutover_at IS NULL"
)


async def insert_order(
    conn: asyncpg.Connection,
    user_id: UUID,
    *,
    status: str = "CREATED",
    quantity: Decimal = Decimal("1"),
    filled_quantity: Decimal = Decimal("0"),
    created_at: datetime | None = None,
) -> UUID:
    return await conn.fetchval(
        """
        INSERT INTO orders (
            user_id, client_order_id, strategy_id, strategy_version, symbol,
            exchange, side, order_type, quantity, status, filled_quantity,
            created_at
        ) VALUES ($1, $2, 'oms-trg-test', '1.0.0', 'BTC/USDT', 'bitget', 'BUY',
                  'MARKET', $3, $4, $5, COALESCE($6, now()))
        RETURNING order_id
        """,
        user_id,
        f"oms-trg-{uuid4().hex}",
        quantity,
        status,
        filled_quantity,
        created_at,
    )


async def insert_event(
    conn: asyncpg.Connection, order_id: UUID, *, from_status: str, to_status: str, event: str
) -> None:
    await conn.execute(
        """
        INSERT INTO order_events (
            order_id, from_status, to_status, event, actor_subject_id, trace_id,
            occurred_at, payload_hash
        ) VALUES ($1, $2, $3, $4, 'system', $5, now(), $6)
        """,
        order_id,
        from_status,
        to_status,
        event,
        uuid4(),
        "e" * 64,
    )
