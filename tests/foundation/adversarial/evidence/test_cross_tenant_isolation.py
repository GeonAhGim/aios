"""Audit Evidence adversarial 테스트 — 79번 §5 AUD-005 "tenant/access role
cannot infer foreign event/evidence existence"."""

from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.application.append_audit_event import append_audit_event
from src.foundation.evidence.application.get_audit_timeline import get_audit_timeline
from src.foundation.evidence.contracts.v1 import Outcome, RecordAuditEventCommand
from tests.integration.conftest import create_test_tenant


class _RaisingRepository:
    """Failure-injection stub — simulates the DB dependency failing mid-query
    (79th §5 AUD-005: a query failure must fail closed, never fall back to a
    silently-empty page that would be indistinguishable from "tenant has no
    events")."""

    async def list_timeline(self, tenant_id, *, cursor, limit, aggregate_type=None, action=None):
        raise asyncpg.PostgresConnectionError("simulated connection loss")


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresAuditEventRepository(pool)


async def test_tenant_timeline_never_includes_another_tenants_events(pool, repo):
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)

    await append_audit_event(
        repo,
        RecordAuditEventCommand(
            tenant_id=tenant_a,
            aggregate_type="mandate_revision",
            aggregate_id=uuid4(),
            action="mandate_activated",
            outcome=Outcome.SUCCESS,
            actor_subject_id=tenant_a,
            trace_id=uuid4(),
            payload={},
        ),
    )

    page_b = await get_audit_timeline(repo, tenant_id=tenant_b)

    assert page_b.items == []


async def test_system_events_are_not_visible_in_any_tenant_timeline(pool, repo):
    """tenant_id=None(system) 체인은 어떤 사용자의 timeline에도 섞이지
    않는다 — 79번 §1 "system 이벤트"와 사용자 timeline은 서로 다른 체인."""
    tenant_id = await create_test_tenant(pool)
    await append_audit_event(
        repo,
        RecordAuditEventCommand(
            tenant_id=None,
            aggregate_type="watchdog",
            aggregate_id=uuid4(),
            action="heartbeat_recorded",
            outcome=Outcome.SUCCESS,
            actor_subject_id=None,
            trace_id=uuid4(),
            payload={},
        ),
    )

    page = await get_audit_timeline(repo, tenant_id=tenant_id)

    assert page.items == []


async def test_forged_cursor_from_another_tenant_does_not_leak_events(pool, repo):
    """tenant A의 cursor(sequence_no)를 tenant B의 timeline 조회에 그대로
    끼워 넣어도(cursor 위조) tenant B에는 자기 자신 이벤트 이외에 아무것도
    새어 나오지 않는다 — cursor는 opaque해야 한다(79번 §3)."""
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)

    await append_audit_event(
        repo,
        RecordAuditEventCommand(
            tenant_id=tenant_a,
            aggregate_type="mandate_revision",
            aggregate_id=uuid4(),
            action="mandate_activated",
            outcome=Outcome.SUCCESS,
            actor_subject_id=tenant_a,
            trace_id=uuid4(),
            payload={},
        ),
    )
    page_a = await get_audit_timeline(repo, tenant_id=tenant_a)
    assert page_a.items != []
    forged_cursor = str(page_a.items[0].sequence_no + 1)

    page_b = await get_audit_timeline(repo, tenant_id=tenant_b, cursor=forged_cursor)

    assert page_b.items == []


async def test_nonexistent_tenant_id_returns_empty_not_error(pool, repo):
    """DB에 한 번도 등록된 적 없는 임의의 tenant_id로 조회해도 예외 없이
    빈 페이지를 반환한다 — "tenant 존재/부재"를 에러 유무로 구분할 수 있으면
    tenant 존재를 추론하는 사이드채널이 된다(79번 §5 AUD-005)."""
    never_created_tenant = uuid4()

    page = await get_audit_timeline(repo, tenant_id=never_created_tenant)

    assert page.items == []
    assert page.next_cursor is None


async def test_aggregate_type_filter_does_not_cross_tenant_boundary(pool, repo):
    """같은 aggregate_type/action으로 필터링해도 다른 tenant가 기록한
    이벤트는 절대 섞이지 않는다 — 필터 파라미터가 tenant 격리를 우회하는
    통로가 되어서는 안 된다."""
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)

    await append_audit_event(
        repo,
        RecordAuditEventCommand(
            tenant_id=tenant_a,
            aggregate_type="mandate_revision",
            aggregate_id=uuid4(),
            action="mandate_activated",
            outcome=Outcome.SUCCESS,
            actor_subject_id=tenant_a,
            trace_id=uuid4(),
            payload={},
        ),
    )

    page_b = await get_audit_timeline(
        repo,
        tenant_id=tenant_b,
        aggregate_type="mandate_revision",
        action="mandate_activated",
    )

    assert page_b.items == []


async def test_repository_failure_propagates_instead_of_returning_empty_page():
    """의존성(DB connection)이 실패하면 get_audit_timeline은 그 예외를
    그대로 전파해야 한다 — 실패를 삼키고 빈 page를 반환하면 "tenant에
    이벤트 없음"과 "조회 자체가 실패함"이 구분되지 않아 fail-closed 원칙
    (CLAUDE.md §3)을 위반한다."""
    with pytest.raises(asyncpg.PostgresConnectionError):
        await get_audit_timeline(_RaisingRepository(), tenant_id=uuid4())
