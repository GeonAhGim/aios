"""FND-03 Audit Event 통합테스트 — 실제 dev DB 대상. 71번 §7 "정상 흐름 +
negative test"."""

import asyncio
import dataclasses
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
from src.foundation.evidence.domain.rules import (
    ChainIntegrityError,
    UnsafePayloadError,
    verify_chain,
)
from tests.integration.conftest import create_test_tenant


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
    tenant_id = await create_test_tenant(pool)
    view = await append_audit_event(repo, _command(tenant_id))
    assert view.sequence_no == 1
    assert view.previous_hash is None
    assert view.event_hash


async def test_second_event_links_to_first(pool, repo):
    tenant_id = await create_test_tenant(pool)
    first = await append_audit_event(repo, _command(tenant_id))
    second = await append_audit_event(repo, _command(tenant_id))
    assert second.sequence_no == 2
    assert second.previous_hash == first.event_hash


async def test_different_tenants_have_independent_chains(pool, repo):
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    view_a = await append_audit_event(repo, _command(tenant_a))
    view_b = await append_audit_event(repo, _command(tenant_b))
    assert view_a.sequence_no == 1
    assert view_b.sequence_no == 1  # 서로 다른 체인이라 둘 다 1부터 시작


async def test_unsafe_payload_key_is_rejected_end_to_end(pool, repo):
    tenant_id = await create_test_tenant(pool)
    with pytest.raises(UnsafePayloadError):
        await append_audit_event(
            repo, _command(tenant_id, payload={"password": "should-not-be-here"})
        )


async def test_nested_unsafe_payload_key_is_rejected_end_to_end(pool, repo):
    """AUD-004는 재귀적으로 중첩 dict도 검사해야 한다 — 1단계 키만 방어하면
    호출자가 `{"meta": {"api_key": ...}}`처럼 한 단계만 감싸서 우회할 수 있다."""
    tenant_id = await create_test_tenant(pool)
    with pytest.raises(UnsafePayloadError):
        await append_audit_event(
            repo,
            _command(tenant_id, payload={"meta": {"api_key": "should-not-be-here"}}),
        )


async def test_append_never_writes_a_row_when_payload_is_unsafe(pool, repo):
    """UnsafePayloadError는 repo.append_event 호출 전에 raise돼야 한다 — 불안전한
    payload가 먼저 검사 없이 커밋되면 체인에 비밀값이 영구히 남는다(fail-closed)."""
    tenant_id = await create_test_tenant(pool)
    with pytest.raises(UnsafePayloadError):
        await append_audit_event(repo, _command(tenant_id, payload={"token": "should-not-be-here"}))
    events = await repo.list_chain_for_verification(tenant_id)
    assert events == []


async def test_append_event_failure_propagates_without_being_swallowed(pool, repo, monkeypatch):
    """실패주입 — repo.append_event가 예외를 던지면(예: DB 연결 끊김)
    append_audit_event은 그 예외를 삼키지 않고 그대로 전파해야 한다
    (fail-closed 기본 원칙, CLAUDE.md §3)."""
    tenant_id = await create_test_tenant(pool)

    async def _boom(**kwargs):
        raise asyncpg.PostgresConnectionError("simulated connection loss")

    monkeypatch.setattr(repo, "append_event", _boom)

    with pytest.raises(asyncpg.PostgresConnectionError):
        await append_audit_event(repo, _command(tenant_id))


async def test_concurrent_appends_for_same_tenant_form_one_unbroken_chain(pool, repo):
    """AUD-003과 직결 — advisory lock이 없으면 동시 append가 같은
    previous_hash를 보고 분기(fork)할 수 있다. N개를 동시에 보내고 나서
    체인 전체가 검증 가능해야 한다(구멍도 분기도 없이 sequence_no 1..N)."""
    tenant_id = await create_test_tenant(pool)
    concurrency = 10

    await asyncio.gather(
        *(append_audit_event(repo, _command(tenant_id)) for _ in range(concurrency))
    )

    await verify_audit_chain(repo, tenant_id)  # 예외 없으면 통과
    events = await repo.list_chain_for_verification(tenant_id)
    assert [e.sequence_no for e in events] == list(range(1, concurrency + 1))


async def test_verify_chain_passes_for_untampered_history(pool, repo):
    tenant_id = await create_test_tenant(pool)
    for _ in range(3):
        await append_audit_event(repo, _command(tenant_id))
    await verify_audit_chain(repo, tenant_id)


async def test_timeline_pagination_and_filters(pool, repo):
    tenant_id = await create_test_tenant(pool)
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


async def test_verify_chain_detects_tampered_event_hash(pool, repo):
    """AUD-003 negative — 저장된 event_hash가 필드로부터 재계산한 값과 다르면
    (변조 의심) verify_chain은 조용히 통과하지 않고 ChainIntegrityError를 던져야
    한다. 실 DB는 append-only라 직접 변조할 수 없으므로, DB에서 읽은 체인을
    메모리에서 한 건 손상시켜 순수 함수 verify_chain에 전달한다."""

    tenant_id = await create_test_tenant(pool)
    await append_audit_event(repo, _command(tenant_id))
    await append_audit_event(repo, _command(tenant_id))

    events = await repo.list_chain_for_verification(tenant_id)
    tampered = list(events)
    tampered[1] = dataclasses.replace(tampered[1], payload_hash="0" * 64)

    with pytest.raises(ChainIntegrityError):
        verify_chain(tampered)


async def test_verify_chain_detects_sequence_no_mismatch(pool, repo):
    """AUD-003 negative — sequence_no가 연속되지 않으면(예: 1, 3으로 건너뜀)
    verify_chain은 ChainIntegrityError를 던져야 한다. DB는 append-only라
    직접 시퀀스를 조작할 수 없으므로, 정상 체인을 만든 뒤 sequence_no를
    메모리에서 손상시켜 verify_chain에 전달한다."""

    tenant_id = await create_test_tenant(pool)
    await append_audit_event(repo, _command(tenant_id))
    await append_audit_event(repo, _command(tenant_id))
    await append_audit_event(repo, _command(tenant_id))

    events = await repo.list_chain_for_verification(tenant_id)
    # sequence_no 2를 4로 변경 → 1, 4, 3이 되어 시퀀스 불연속
    tampered = [
        events[0],
        dataclasses.replace(events[1], sequence_no=4),
        events[2],
    ]

    with pytest.raises(ChainIntegrityError):
        verify_chain(tampered)


async def test_verify_chain_detects_previous_hash_mismatch(pool, repo):
    """AUD-003 negative — previous_hash가 이전 이벤트의 event_hash와 다르면
    verify_chain은 ChainIntegrityError를 던져야 한다. DB 변조 대신
    메모리에서 previous_hash만 변경한다."""

    tenant_id = await create_test_tenant(pool)
    await append_audit_event(repo, _command(tenant_id))
    await append_audit_event(repo, _command(tenant_id))

    events = await repo.list_chain_for_verification(tenant_id)
    # 두 번째 이벤트의 previous_hash를 임의 값으로 변경
    tampered = [
        events[0],
        dataclasses.replace(events[1], previous_hash="0" * 64),
    ]

    with pytest.raises(ChainIntegrityError):
        verify_chain(tampered)


async def test_unknown_evidence_lookup_returns_none(pool, repo):
    """unknown evidence lookup rejected — 존재하지 않는 event ID로 조회하면
    None을 반환해야 한다 (fail-closed: 존재하지 않는 증거를 허용하지 않음)."""

    tenant_id = await create_test_tenant(pool)
    await append_audit_event(repo, _command(tenant_id))

    # get_latest_event는 aggregate_type/aggregate_id/action 기반이므로
    # 다른 aggregate로 조회하면 None이어야 한다
    fake_id = uuid4()
    result = await repo.get_latest_event(
        aggregate_type="nonexistent_type",
        aggregate_id=fake_id,
        action="nonexistent_action",
    )
    assert result is None


async def test_append_event_failure_injection_repo_raises(pool, repo, monkeypatch):
    """failure-injection: repo.append_event가 예외를 던지면 append_audit_event도
    같은 예외를 전파해야 한다. monkeypatch는 테스트 종료 후 자동 복원된다.
    원본 복원은 monkeypatch.setattr cleanup hook이 맡는다."""

    tenant_id = await create_test_tenant(pool)

    async def _failing_append(*args, **kwargs):
        raise ConnectionError("simulated DB connection failure")

    monkeypatch.setattr(repo, "append_event", _failing_append)

    with pytest.raises(ConnectionError, match="simulated DB connection failure"):
        await append_audit_event(repo, _command(tenant_id))

    # monkeypatch는 테스트 종료 후 자동 복원되므로, 같은 테스트 내에서
    # 원본 동작을 검증하려면 별도 새 repo 인스턴스를 쓴다.
    fresh_repo = PostgresAuditEventRepository(pool)
    await append_audit_event(fresh_repo, _command(tenant_id))
    events = await fresh_repo.list_chain_for_verification(tenant_id)
    assert len(events) == 1
