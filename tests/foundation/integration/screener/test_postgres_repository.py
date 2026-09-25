"""U-1a `PostgresSavedScreenerRepository` 통합 테스트(실 DB, TEST_DATABASE_URL).

Spec: task-2628(U-1a) decision(저장·테넌트 상한 50), standard-105(동시성).
"""

from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.screener.adapters.postgres_repository import (
    PostgresSavedScreenerRepository,
)
from src.foundation.screener.contracts.v1 import (
    MAX_SAVED_SCREENERS_PER_TENANT,
    IndicatorFilter,
    ScreenDefinition,
)
from src.foundation.screener.ports.repository import (
    SavedScreenerLimitError,
    SavedScreenerNameConflictError,
)
from tests.integration.conftest import create_test_tenant


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def repo(pool: asyncpg.Pool) -> PostgresSavedScreenerRepository:
    return PostgresSavedScreenerRepository(pool)


def _definition() -> ScreenDefinition:
    return ScreenDefinition(
        universe="kr_stocks",
        filters=(IndicatorFilter(condition="ta.rsi(close, 14) < 30"),),
    )


async def test_save_then_get_round_trips(pool, repo) -> None:
    tenant_id = await create_test_tenant(pool)
    definition = _definition()

    saved = await repo.save(tenant_id=tenant_id, name="my screen", definition=definition)
    fetched = await repo.get(tenant_id, saved.id)

    assert fetched is not None
    assert fetched.name == "my screen"
    assert fetched.definition == definition


async def test_list_for_tenant_returns_only_own_rows(pool, repo) -> None:
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    await repo.save(tenant_id=tenant_a, name="a1", definition=_definition())
    await repo.save(tenant_id=tenant_b, name="b1", definition=_definition())

    rows_a = await repo.list_for_tenant(tenant_a)

    assert [r.name for r in rows_a] == ["a1"]


# ---- negative: 교차 테넌트 조회/삭제는 404(None/False)로 취급한다 ----


async def test_cross_tenant_get_returns_none(pool, repo) -> None:
    owner = await create_test_tenant(pool)
    stranger = await create_test_tenant(pool)
    saved = await repo.save(tenant_id=owner, name="mine", definition=_definition())

    assert await repo.get(stranger, saved.id) is None


async def test_cross_tenant_delete_returns_false_and_does_not_delete(pool, repo) -> None:
    owner = await create_test_tenant(pool)
    stranger = await create_test_tenant(pool)
    saved = await repo.save(tenant_id=owner, name="mine", definition=_definition())

    deleted = await repo.delete(stranger, saved.id)

    assert deleted is False
    assert await repo.get(owner, saved.id) is not None


# ---- negative: 같은 테넌트 안에서 이름 중복 ----


async def test_duplicate_name_within_tenant_is_rejected(pool, repo) -> None:
    tenant_id = await create_test_tenant(pool)
    await repo.save(tenant_id=tenant_id, name="dup", definition=_definition())

    with pytest.raises(SavedScreenerNameConflictError):
        await repo.save(tenant_id=tenant_id, name="dup", definition=_definition())


# ---- negative: 테넌트당 저장 상한(50) 초과 ----


async def test_saved_screener_cap_is_enforced(pool, repo) -> None:
    tenant_id = await create_test_tenant(pool)
    for i in range(MAX_SAVED_SCREENERS_PER_TENANT):
        await repo.save(tenant_id=tenant_id, name=f"screen-{i}", definition=_definition())

    with pytest.raises(SavedScreenerLimitError):
        await repo.save(tenant_id=tenant_id, name="one-too-many", definition=_definition())

    rows = await repo.list_for_tenant(tenant_id)
    assert len(rows) == MAX_SAVED_SCREENERS_PER_TENANT


# ---- failure injection: 삭제/조회 대상이 아예 없는 id ----


async def test_get_unknown_id_returns_none(pool, repo) -> None:
    tenant_id = await create_test_tenant(pool)

    assert await repo.get(tenant_id, uuid4()) is None


async def test_delete_unknown_id_returns_false(pool, repo) -> None:
    tenant_id = await create_test_tenant(pool)

    assert await repo.delete(tenant_id, uuid4()) is False
