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
from tests.integration.conftest import create_test_user


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
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)

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
    tenant_id = await create_test_user(pool)
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
