"""FD-7.2 통합테스트 — audit_log 조회, 실제 dev DB 대상."""
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.logging.audit_log import record_audit_log
from src.services.audit_log_read_service import AuditLogReadService


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
def service(pool):
    return AuditLogReadService(pool)


async def _record(pool, *, action_type: str, target_type: str, target_id: str) -> None:
    async with pool.acquire() as conn:
        await record_audit_log(
            conn,
            actor_agent="test-actor",
            action_type=action_type,
            decision_data={"note": "test"},
            target_type=target_type,
            target_id=target_id,
        )


async def test_list_entries_returns_recorded_entry(service, pool):
    marker = uuid4().hex[:8]
    await _record(pool, action_type=f"test.action.{marker}", target_type="test", target_id=marker)

    page = await service.list_entries(action_type=f"test.action.{marker}")

    assert page.total == 1
    assert page.items[0].target_id == marker
    assert page.items[0].decision_data == {"note": "test"}


async def test_list_entries_filters_by_target(service, pool):
    marker = uuid4().hex[:8]
    other_marker = uuid4().hex[:8]
    await _record(pool, action_type="test.filter", target_type="widget", target_id=marker)
    await _record(pool, action_type="test.filter", target_type="widget", target_id=other_marker)

    page = await service.list_entries(target_type="widget", target_id=marker)

    assert all(item.target_id == marker for item in page.items)


async def test_list_entries_paginates(service, pool):
    marker = uuid4().hex[:8]
    action_type = f"test.page.{marker}"
    for _ in range(3):
        await _record(pool, action_type=action_type, target_type="widget", target_id=marker)

    page = await service.list_entries(action_type=action_type, page=1, page_size=2)

    assert page.total == 3
    assert len(page.items) == 2
