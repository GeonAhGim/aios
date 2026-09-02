"""FND-03 Audit Event 통합테스트 — 실제 dev DB 대상. 71번 §7 "정상 흐름 +
negative test"."""
import asyncio
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.application.append_audit_event import append_audit_event
from src.foundation.evidence.application.get_audit_timeline import get_audit_timeline
from src.foundation.evidence.application.verify_audit_chain import verify_audit_chain
from src.foundation.evidence.contracts.v1 import Classification, Outcome, RecordAuditEventCommand
from src.foundation.evidence.domain.rules import UnsafePayloadError
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresAuditEventRepository(pool)


def _command(tenant_id, **overrides) -> RecordAuditEventCommand:
    defaults = dict(
        tenant_id=tenant_id,
        aggregate_type="mandate_revision",
        aggregate_id=uuid4(),
        aggregate_revision=1,
        action="mandate_activated",
        outcome=Outcome.SUCCESS,
        actor_subject_id=tenant_id,
        trace_id=uuid4(),
        payload={"revision_no": 1},
        classification=Classification.INTERNAL,
    )
    defaults.update(overrides)
    return RecordAuditEventCommand(**defaults)


async def test_first_event_has_no_previous_hash(pool, repo):
    tenant_id = await create_test_user(pool)
    view = await append_audit_event(repo, _command(tenant_id))
    assert view.sequence_no == 1
    assert view.previous_hash is None
    assert view.event_hash


async def test_second_event_links_to_first(pool, repo):
    tenant_id = await create_test_user(pool)
    first = await append_audit_event(repo, _command(tenant_id))
    second = await append_audit_event(repo, _command(tenant_id))
    assert second.sequence_no == 2
    assert second.previous_hash == first.event_hash


async def test_different_tenants_have_independent_chains(pool, repo):
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)
    view_a = await append_audit_event(repo, _command(tenant_a))
    view_b = await append_audit_event(repo, _command(tenant_b))
    assert view_a.sequence_no == 1
    assert view_b.sequence_no == 1  # 서로 다른 체인이라 둘 다 1부터 시작


async def test_unsafe_payload_key_is_rejected_end_to_end(pool, repo):
    tenant_id = await create_test_user(pool)
    with pytest.raises(UnsafePayloadError):
        await append_audit_event(
            repo, _command(tenant_id, payload={"password": "should-not-be-here"})
        )


async def test_concurrent_appends_for_same_tenant_form_one_unbroken_chain(pool, repo):
    """AUD-003과 직결 — advisory lock이 없으면 동시 append가 같은
    previous_hash를 보고 분기(fork)할 수 있다. N개를 동시에 보내고 나서
    체인 전체가 검증 가능해야 한다(구멍도 분기도 없이 sequence_no 1..N)."""
    tenant_id = await create_test_user(pool)
    concurrency = 10

    await asyncio.gather(
        *(append_audit_event(repo, _command(tenant_id)) for _ in range(concurrency))
    )

    await verify_audit_chain(repo, tenant_id)  # 예외 없으면 통과
    events = await repo.list_chain_for_verification(tenant_id)
    assert [e.sequence_no for e in events] == list(range(1, concurrency + 1))


async def test_verify_chain_passes_for_untampered_history(pool, repo):
    tenant_id = await create_test_user(pool)
    for _ in range(3):
        await append_audit_event(repo, _command(tenant_id))
    await verify_audit_chain(repo, tenant_id)


async def test_timeline_pagination_and_filters(pool, repo):
    tenant_id = await create_test_user(pool)
    await append_audit_event(repo, _command(tenant_id, action="a"))
    await append_audit_event(repo, _command(tenant_id, action="b"))
    await append_audit_event(repo, _command(tenant_id, action="a"))

    page = await get_audit_timeline(repo, tenant_id=tenant_id, limit=2)
    assert len(page.items) == 2
    assert page.next_cursor is not None
    assert [i.sequence_no for i in page.items] == [3, 2]  # 최신순

    next_page = await get_audit_timeline(repo, tenant_id=tenant_id, cursor=page.next_cursor)
    assert [i.sequence_no for i in next_page.items] == [1]
    assert next_page.next_cursor is None

    filtered = await get_audit_timeline(repo, tenant_id=tenant_id, action="a")
    assert {i.sequence_no for i in filtered.items} == {1, 3}
