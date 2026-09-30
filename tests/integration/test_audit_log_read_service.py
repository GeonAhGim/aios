"""FD-7.2 통합테스트 — audit_log 조회, 실제 dev DB 대상."""

from pathlib import Path
from unittest.mock import MagicMock
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


async def test_list_entries_no_match_returns_empty_page(service):
    marker = uuid4().hex[:8]

    page = await service.list_entries(action_type=f"test.nonexistent.{marker}")

    assert page.total == 0
    assert page.items == []


async def test_list_entries_missing_verification_chain_is_none(service, pool):
    marker = uuid4().hex[:8]
    action_type = f"test.nochain.{marker}"
    await _record(pool, action_type=action_type, target_type="widget", target_id=marker)

    page = await service.list_entries(action_type=action_type)

    assert page.items[0].verification_chain is None


async def test_list_entries_rejects_page_zero(service, pool):
    marker = uuid4().hex[:8]
    action_type = f"test.pagezero.{marker}"
    await _record(pool, action_type=action_type, target_type="widget", target_id=marker)

    with pytest.raises(asyncpg.exceptions.InvalidRowCountInResultOffsetClauseError):
        await service.list_entries(action_type=action_type, page=0)


async def test_list_entries_rejects_negative_page(service, pool):
    marker = uuid4().hex[:8]
    action_type = f"test.negpage.{marker}"
    await _record(pool, action_type=action_type, target_type="widget", target_id=marker)

    with pytest.raises(asyncpg.exceptions.InvalidRowCountInResultOffsetClauseError):
        await service.list_entries(action_type=action_type, page=-1)


async def test_list_entries_rejects_negative_page_size(service, pool):
    marker = uuid4().hex[:8]
    action_type = f"test.negpagesize.{marker}"
    await _record(pool, action_type=action_type, target_type="widget", target_id=marker)

    with pytest.raises(asyncpg.exceptions.InvalidRowCountInLimitClauseError):
        await service.list_entries(action_type=action_type, page=1, page_size=-1)


async def test_record_audit_log_rejects_action_type_over_varchar_limit(pool):
    """negative -- `audit_log.action_type`은 VARCHAR(50)(9ec8a1ee28d7). 이
    한계를 넘는 값은 read service가 아니라 DB 제약이 직접 거부해야 한다 —
    list_entries가 애초에 조회할 수 없는 부정 상태가 쓰기 시점에 fail-closed로
    막힌다는 것을 확인한다."""
    marker = uuid4().hex[:8]
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.StringDataRightTruncationError):
            await record_audit_log(
                conn,
                actor_agent="test-actor",
                action_type=f"test.overflow.{marker}." + "x" * 60,
                decision_data={"note": "test"},
                target_type="test",
                target_id=marker,
            )


async def test_list_entries_page_size_zero_returns_no_items(service, pool):
    marker = uuid4().hex[:8]
    action_type = f"test.pagesizezero.{marker}"
    await _record(pool, action_type=action_type, target_type="widget", target_id=marker)

    page = await service.list_entries(action_type=action_type, page=1, page_size=0)

    assert page.total == 1
    assert page.items == []


async def test_list_entries_propagates_pool_acquire_failure():
    fake_pool = MagicMock()
    fake_pool.acquire.side_effect = RuntimeError("pool exhausted")
    service = AuditLogReadService(fake_pool)

    with pytest.raises(RuntimeError, match="pool exhausted"):
        await service.list_entries()
