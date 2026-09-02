"""L0-4 — `append_event_in(conn, ...)`가 외부 트랜잭션 경계를 실제로 따르는지
검증한다. 105번 §5.1: `post_entry`류 상위 트랜잭션이 실패해 롤백되면 그 안에서
쓴 감사 이벤트도 함께 사라져야 한다(별도 커넥션으로 커밋해버리면 안 됨)."""
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.domain.models import Classification, Outcome
from src.foundation.evidence.domain.rules import compute_payload_hash
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


async def _append_in(repo, conn, tenant_id, **overrides):
    payload = overrides.pop("payload", {"k": "v"})
    defaults = dict(
        tenant_id=tenant_id,
        aggregate_type="ledger_entry",
        aggregate_id=uuid4(),
        aggregate_revision=None,
        action="post_entry",
        outcome=Outcome.SUCCESS,
        actor_subject_id=tenant_id,
        trace_id=uuid4(),
        payload_hash=compute_payload_hash(payload),
        payload=payload,
        classification=Classification.INTERNAL,
    )
    defaults.update(overrides)
    return await repo.append_event_in(conn, **defaults)


async def test_event_visible_after_external_transaction_commits(pool, repo):
    tenant_id = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        event = await _append_in(repo, conn, tenant_id)

    events = await repo.list_chain_for_verification(tenant_id)
    assert [e.id for e in events] == [event.id]
    assert event.sequence_no == 1
    assert event.previous_hash is None


async def test_event_disappears_when_external_transaction_rolls_back(pool, repo):
    """DoD: "외부 트랜잭션 롤백 시 이벤트도 사라짐" — append_event_in이 자체
    커넥션·트랜잭션을 열어 몰래 커밋해버리면 이 테스트가 실패한다."""
    tenant_id = await create_test_user(pool)

    class _BoomError(Exception):
        pass

    with pytest.raises(_BoomError):
        async with pool.acquire() as conn, conn.transaction():
            await _append_in(repo, conn, tenant_id)
            raise _BoomError("caller-side failure after append_event_in")

    events = await repo.list_chain_for_verification(tenant_id)
    assert events == []


async def test_second_event_in_same_external_transaction_links_to_first(pool, repo):
    tenant_id = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        first = await _append_in(repo, conn, tenant_id)
        second = await _append_in(repo, conn, tenant_id)

    assert second.sequence_no == 2
    assert second.previous_hash == first.event_hash


async def test_append_event_still_works_standalone_after_refactor(pool, repo):
    """append_event()가 append_event_in()을 내부에서 호출하도록 바뀐 뒤에도
    기존 공개 계약(자체 트랜잭션으로 즉시 커밋)이 유지되는지 확인한다."""
    tenant_id = await create_test_user(pool)
    payload = {"k": "v"}
    event = await repo.append_event(
        tenant_id=tenant_id,
        aggregate_type="ledger_entry",
        aggregate_id=uuid4(),
        aggregate_revision=None,
        action="post_entry",
        outcome=Outcome.SUCCESS,
        actor_subject_id=tenant_id,
        trace_id=uuid4(),
        payload_hash=compute_payload_hash(payload),
        payload=payload,
        classification=Classification.INTERNAL,
    )

    events = await repo.list_chain_for_verification(tenant_id)
    assert [e.id for e in events] == [event.id]
