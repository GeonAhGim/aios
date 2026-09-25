"""task-4694: `src/foundation/evidence/ports/repository.py` — 커버리지 0% 보강.

Spec: docs/specs/L4_evidence_audit_v1.0.md, Rule 79 §1/§3.

`@runtime_checkable` Protocol의 `isinstance()`는 메서드 **이름**만 확인한다 —
파라미터·반환 타입은 mypy(정적)가 확인한다(`tests/foundation/unit/ledger/
test_ports_protocol.py`와 같은 패턴). negative test는 메서드 하나가 빠진
구현마다 isinstance()가 fail-closed로 False를 반환하는지 증명한다. 별도로
`AuditEventRepository`를 직접 서브클래싱해 오버라이드하지 않은 메서드를
호출하면 Protocol 본문의 `...` 문이 실행되는 것(coverage 목적)과, 어댑터가
예외를 던지면 Protocol이 그 예외를 삼키지 않고 그대로 전파하는 것(실패주입)을
함께 검증한다.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.foundation.evidence.domain.models import Classification, Outcome
from src.foundation.evidence.ports.repository import AuditEventRepository


class _FullAuditEventRepo:
    async def append_event(self, **kwargs): ...
    async def list_timeline(
        self, tenant_id, *, cursor, limit, aggregate_type=None, action=None
    ): ...
    async def list_chain_for_verification(self, tenant_id): ...
    async def get_latest_event(self, aggregate_type, aggregate_id, *, action): ...


class _MissingAppendEventRepo:
    """`append_event`가 빠진 불완전 구현 — 포트를 만족하지 못해야 한다."""

    async def list_timeline(
        self, tenant_id, *, cursor, limit, aggregate_type=None, action=None
    ): ...
    async def list_chain_for_verification(self, tenant_id): ...
    async def get_latest_event(self, aggregate_type, aggregate_id, *, action): ...


class _MissingListTimelineRepo:
    """`list_timeline`이 빠진 불완전 구현 — 포트를 만족하지 못해야 한다."""

    async def append_event(self, **kwargs): ...
    async def list_chain_for_verification(self, tenant_id): ...
    async def get_latest_event(self, aggregate_type, aggregate_id, *, action): ...


class _MissingGetLatestEventRepo:
    """`get_latest_event`가 빠진 불완전 구현 — 포트를 만족하지 못해야 한다."""

    async def append_event(self, **kwargs): ...
    async def list_timeline(
        self, tenant_id, *, cursor, limit, aggregate_type=None, action=None
    ): ...
    async def list_chain_for_verification(self, tenant_id): ...


class _StubSubclass(AuditEventRepository):
    """오버라이드 없이 Protocol 본문(`...`)을 그대로 물려받는 서브클래스 —
    본문 statement를 실행해 coverage를 채운다."""


class _RaisingAppendEventRepo(AuditEventRepository):
    """해시체인 잠금 실패를 흉내내는 실패주입용 구현."""

    async def append_event(self, **kwargs):
        raise RuntimeError("advisory lock contention: chain fork risk")


def test_full_implementation_satisfies_protocol() -> None:
    assert isinstance(_FullAuditEventRepo(), AuditEventRepository)


def test_missing_append_event_fails_port_check() -> None:
    assert not isinstance(_MissingAppendEventRepo(), AuditEventRepository)


def test_missing_list_timeline_fails_port_check() -> None:
    assert not isinstance(_MissingListTimelineRepo(), AuditEventRepository)


def test_missing_get_latest_event_fails_port_check() -> None:
    assert not isinstance(_MissingGetLatestEventRepo(), AuditEventRepository)


async def test_default_stub_bodies_are_inert_when_invoked_directly() -> None:
    """`...` 본문을 오버라이드 없이 직접 호출하면 `None`을 반환할 뿐, 예외를
    던지지도 데이터를 만들어내지도 않는다 — 실제 구현은 반드시 adapters/에서
    와야 한다는 계약을 실증한다."""
    stub = _StubSubclass()

    assert (
        await stub.append_event(
            tenant_id=uuid4(),
            aggregate_type="mandate",
            aggregate_id=uuid4(),
            aggregate_revision=1,
            action="activate",
            outcome=Outcome.SUCCESS,
            actor_subject_id=uuid4(),
            trace_id=uuid4(),
            payload_hash="deadbeef",
            payload={},
            classification=Classification.INTERNAL,
        )
        is None
    )
    assert await stub.list_timeline(uuid4(), cursor=None, limit=10) is None
    assert await stub.list_chain_for_verification(None) is None
    assert await stub.get_latest_event("mandate", uuid4(), action="activate") is None


async def test_append_event_failure_propagates_uncaught() -> None:
    """어댑터가 락 경합으로 예외를 던지면 포트는 그것을 삼키지 않는다."""
    repo = _RaisingAppendEventRepo()

    with pytest.raises(RuntimeError, match="advisory lock contention"):
        await repo.append_event(
            tenant_id=None,
            aggregate_type="mandate",
            aggregate_id=uuid4(),
            aggregate_revision=None,
            action="activate",
            outcome=Outcome.ERROR,
            actor_subject_id=None,
            trace_id=uuid4(),
            payload_hash="deadbeef",
            payload={},
            classification=Classification.INTERNAL,
        )
