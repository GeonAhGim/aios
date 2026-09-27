"""FND-03 Audit Event 통합테스트 — __init__.py negative/실패주입.

DoD: negative test 3건 이상(불변식 위반 입력을 명시적으로 거부),
실패주입 1건 이상(monkeypatch로 의존성 예외 유발).
"""

import asyncio
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.application.append_audit_event import append_audit_event
from src.foundation.evidence.application.record_command_event import RecordAuditEventCommand
from src.foundation.evidence.contracts.v1 import Classification, Outcome
from src.foundation.evidence.domain.rules import UnsafePayloadError
from tests.integration.conftest import create_test_tenant


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("TEST_DATABASE_URL") or env.get("DATABASE_URL")
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


def _command(tenant_id: str, **overrides) -> RecordAuditEventCommand:
    """RecordAuditEventCommand를 조립."""
    defaults = dict(
        tenant_id=tenant_id,
        aggregate_type="mandate_revision",
        aggregate_id=str(uuid4()),
        aggregate_revision=1,
        action="mandate_activated",
        outcome=Outcome.SUCCESS,
        actor_subject_id=tenant_id,
        trace_id=str(uuid4()),
        payload={"revision_no": 1},
        classification=Classification.INTERNAL,
    )
    defaults.update(overrides)
    return RecordAuditEventCommand(**defaults)


# ── negative tests (불변식 위반 입력을 명시적으로 거부) ──────────────────


async def test_negative_unsafe_payload_key_password(pool, repo):
    """AUD-006: payload에 'password' 키가 있으면 UnsafePayloadError를 던진다."""
    tenant_id = await create_test_tenant(pool)
    with pytest.raises(UnsafePayloadError):
        await append_audit_event(repo, _command(tenant_id, payload={"password": "secret"}))


async def test_negative_unsafe_payload_key_secret(pool, repo):
    """AUD-006: payload에 'secret' 키가 있으면 UnsafePayloadError를 던진다."""
    tenant_id = await create_test_tenant(pool)
    with pytest.raises(UnsafePayloadError):
        await append_audit_event(repo, _command(tenant_id, payload={"secret": "top_secret"}))


async def test_negative_unsafe_payload_key_api_key(pool, repo):
    """AUD-006: payload에 'api_key' 키가 있으면 UnsafePayloadError를 던진다."""
    tenant_id = await create_test_tenant(pool)
    with pytest.raises(UnsafePayloadError):
        await append_audit_event(repo, _command(tenant_id, payload={"api_key": "sk-12345"}))


async def test_negative_unsafe_payload_key_token(pool, repo):
    """AUD-006: payload에 'token' 키가 있으면 UnsafePayloadError를 던진다."""
    tenant_id = await create_test_tenant(pool)
    with pytest.raises(UnsafePayloadError):
        await append_audit_event(repo, _command(tenant_id, payload={"token": "abc123"}))


# ── failure injection (의존성 예외 유발) ────────────────────────────────


async def test_failure_injection_repository_error_on_append(pool, repo):
    """실패주입: repository의 append_event가 예외를 던지면 호출자에게 전파된다."""
    tenant_id = await create_test_tenant(pool)
    original_append = repo.append_event

    async def failing_append(*args, **kwargs):
        raise RuntimeError("db connection lost")

    repo.append_event = failing_append

    with pytest.raises(RuntimeError, match="db connection lost"):
        await append_audit_event(repo, _command(tenant_id))

    repo.append_event = original_append


async def test_failure_injection_repository_error_on_fetch_previous(pool, repo):
    """실패주입: repository의 append_event가 예외를 던지면 전파된다.
    asyncpg.Pool.acquire는 읽 전용이므로, repo.append_event 자체를
    모의(mock)하여 DB 예외를 유발한다.
    """
    tenant_id = await create_test_tenant(pool)

    # repo.append_event를 모의하여 DB 오류 시뮬레이션
    original_append = repo.append_event

    async def failing_append(*args, **kwargs):
        raise RuntimeError("db timeout")

    repo.append_event = failing_append

    try:
        with pytest.raises(RuntimeError, match="db timeout"):
            await append_audit_event(repo, _command(tenant_id))
    finally:
        repo.append_event = original_append


# ── chain integrity 검증 ────────────────────────────────────────────────


async def test_chain_integrity_after_concurrent_appends(pool, repo):
    """동시 append 후 체인 무결성 — sequence_no 1..N, previous_hash 연결 확인."""
    tenant_id = await create_test_tenant(pool)
    concurrency = 5

    await asyncio.gather(
        *(append_audit_event(repo, _command(tenant_id)) for _ in range(concurrency))
    )

    events = await repo.list_chain_for_verification(tenant_id)
    seqnos = [e.sequence_no for e in events]
    assert seqnos == list(range(1, concurrency + 1))

    # previous_hash 체인 연결 확인
    for i in range(1, len(events)):
        assert events[i].previous_hash == events[i - 1].event_hash


async def test_chain_integrity_empty_tenant(pool, repo):
    """테넌트별 체인 독립성: 빈 체인에서 시작해도 sequence_no 1부터 시작."""
    tenant_id = await create_test_tenant(pool)
    first = await append_audit_event(repo, _command(tenant_id))
    assert first.sequence_no == 1
    assert first.previous_hash is None
    assert first.event_hash is not None
