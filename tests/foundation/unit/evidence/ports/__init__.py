"""DEEPEN(task-10290) — AuditEventRepository 포트: negative / failure-injection 보강.

원 리프 task-7301(고아 산출물 회수) 당시 이 파일은 패키지 마커로만 남아
negative test 0건이었다. 같은 디렉터리의 `test_ports_protocol.py`는 구조적
isinstance 계약만 다루고, `tests/foundation/evidence/ports/*`는 실제 검증
로직 없는 pass-through 스텁만 사용한다 — 어느 쪽도 "fail-closed 저장소가
불변식 위반 입력을 실제로 거부하는지"는 증명하지 않는다. 이 파일은 그 간극을
메운다: sequence_no 단조성 / event_hash 변조 / unsafe payload 키를 실제로
검증하는 in-memory 구현을 두고, 그 구현이 거부하는지와 예외를 삼키지 않는지를
검증한다.

Invariant: I-10 ("구현됨 ≠ 작동함") — 안전/정책 배선은 적대적 통합 테스트로
증명해야 한다(docs/design/INVARIANTS.md).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome
from src.foundation.evidence.domain.rules import (
    UnsafePayloadError,
    assert_safe_payload,
    compute_event_hash,
    compute_payload_hash,
)
from src.foundation.evidence.ports.repository import AuditEventRepository

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_event(
    *,
    tenant_id: UUID | None,
    sequence_no: int,
    previous_hash: str | None,
    aggregate_type: str = "mandate",
    action: str = "activate",
    outcome: Outcome = Outcome.SUCCESS,
    classification: Classification = Classification.INTERNAL,
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    """실제 `compute_event_hash`로 해시를 계산한 유효한 AuditEvent를 만든다."""
    p = payload if payload is not None else {}
    payload_hash = compute_payload_hash(p)
    aggregate_id = uuid4()
    event_hash = compute_event_hash(
        previous_hash=previous_hash,
        tenant_id=tenant_id,
        sequence_no=sequence_no,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        action=action,
        outcome=outcome,
        payload_hash=payload_hash,
        classification=classification,
        occurred_at=_NOW,
    )
    return AuditEvent(
        id=uuid4(),
        tenant_id=tenant_id,
        sequence_no=sequence_no,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_revision=1,
        action=action,
        outcome=outcome,
        actor_subject_id=uuid4(),
        trace_id=uuid4(),
        payload_hash=payload_hash,
        payload=p,
        classification=classification,
        previous_hash=previous_hash,
        event_hash=event_hash,
        occurred_at=_NOW,
    )


class _ValidatingInMemoryRepo:
    """Rule 79 §1 체인 불변식을 실제로 검증하는 in-memory 구현.

    `tests/foundation/evidence/ports/test_repository_protocol_contract.py`의
    스텁들과 달리 append_event가 sequence_no 단조성/event_hash 재계산/unsafe
    payload 키를 전부 거부한다 — fail-closed 배선을 증명하는 용도."""

    def __init__(self) -> None:
        self._chains: dict[str, list[AuditEvent]] = {}

    @staticmethod
    def _chain_key(tenant_id: UUID | None) -> str:
        return "system" if tenant_id is None else str(tenant_id)

    async def append_event(self, **kwargs: Any) -> AuditEvent:
        tenant_id = kwargs["tenant_id"]
        sequence_no = kwargs["sequence_no"] if "sequence_no" in kwargs else None
        payload = kwargs["payload"]
        assert_safe_payload(payload)

        key = self._chain_key(tenant_id)
        chain = self._chains.setdefault(key, [])
        prev_event = chain[-1] if chain else None
        prev_hash = prev_event.event_hash if prev_event is not None else None
        next_seq = (prev_event.sequence_no + 1) if prev_event is not None else 1

        if sequence_no is None:
            sequence_no = next_seq
        if sequence_no != next_seq:
            raise ValueError(f"sequence_no {sequence_no} must equal {next_seq} for chain {key}")

        payload_hash = compute_payload_hash(payload)
        expected_hash = compute_event_hash(
            previous_hash=prev_hash,
            tenant_id=tenant_id,
            sequence_no=sequence_no,
            aggregate_type=kwargs["aggregate_type"],
            aggregate_id=kwargs["aggregate_id"],
            action=kwargs["action"],
            outcome=kwargs["outcome"],
            payload_hash=payload_hash,
            classification=kwargs["classification"],
            occurred_at=_NOW,
        )
        event = AuditEvent(
            id=uuid4(),
            tenant_id=tenant_id,
            sequence_no=sequence_no,
            aggregate_type=kwargs["aggregate_type"],
            aggregate_id=kwargs["aggregate_id"],
            aggregate_revision=kwargs["aggregate_revision"],
            action=kwargs["action"],
            outcome=kwargs["outcome"],
            actor_subject_id=kwargs["actor_subject_id"],
            trace_id=kwargs["trace_id"],
            payload_hash=payload_hash,
            payload=payload,
            classification=kwargs["classification"],
            previous_hash=prev_hash,
            event_hash=expected_hash,
            occurred_at=_NOW,
        )
        chain.append(event)
        return event

    async def append_existing_event(self, event: AuditEvent) -> None:
        """테스트 헬퍼 — 미리 만든(변조 가능성 있는) 이벤트를 검증 경로에 그대로 통과시킨다."""
        key = self._chain_key(event.tenant_id)
        chain = self._chains.setdefault(key, [])
        prev_event = chain[-1] if chain else None
        prev_hash = prev_event.event_hash if prev_event is not None else None
        next_seq = (prev_event.sequence_no + 1) if prev_event is not None else 1

        if event.sequence_no != next_seq:
            raise ValueError(
                f"sequence_no {event.sequence_no} must equal {next_seq} for chain {key}"
            )
        recomputed = compute_event_hash(
            previous_hash=prev_hash,
            tenant_id=event.tenant_id,
            sequence_no=event.sequence_no,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            action=event.action,
            outcome=event.outcome,
            payload_hash=event.payload_hash,
            classification=event.classification,
            occurred_at=_NOW,
        )
        if recomputed != event.event_hash:
            raise ValueError(
                f"event_hash mismatch for sequence_no={event.sequence_no}: "
                f"expected {recomputed}, got {event.event_hash}"
            )
        chain.append(event)

    async def list_timeline(
        self,
        tenant_id: UUID,
        *,
        cursor: str | None,
        limit: int,
        aggregate_type: str | None = None,
        action: str | None = None,
    ) -> tuple[list[AuditEvent], str | None]:
        chain = self._chains.get(self._chain_key(tenant_id), [])
        return chain[:limit], None

    async def list_chain_for_verification(self, tenant_id: UUID | None) -> list[AuditEvent]:
        return list(self._chains.get(self._chain_key(tenant_id), []))

    async def get_latest_event(
        self, aggregate_type: str, aggregate_id: UUID, *, action: str
    ) -> AuditEvent | None:
        for chain in self._chains.values():
            for event in reversed(chain):
                if (
                    event.aggregate_type == aggregate_type
                    and event.aggregate_id == aggregate_id
                    and event.action == action
                ):
                    return event
        return None


# ── protocol conformance ─────────────────────────────────────────────────────


def test_validating_repo_satisfies_protocol() -> None:
    assert isinstance(_ValidatingInMemoryRepo(), AuditEventRepository)


def test_missing_list_chain_for_verification_fails_port_check() -> None:
    """구조적 negative: list_chain_for_verification이 빠지면 Protocol을
    만족하지 못해야 한다 — AUD-003 체인 검증 경로가 배선되지 않은 구현을
    fail-closed로 걸러낸다."""

    class _MissingListChainRepo:
        async def append_event(self, **kwargs: Any) -> None:
            return None

        async def list_timeline(
            self, tenant_id: UUID, *, cursor: str | None, limit: int, **kw: Any
        ) -> None:
            return None

        async def get_latest_event(
            self, aggregate_type: str, aggregate_id: UUID, *, action: str
        ) -> None:
            return None

    assert not isinstance(_MissingListChainRepo(), AuditEventRepository)


# ── negative tests (>=3) ─────────────────────────────────────────────────────


class TestNegativeSequenceInvariant:
    """sequence_no 단조성 위반은 append 경로에서 거부되어야 한다(Rule 79 §1)."""

    async def test_rejects_duplicate_sequence_no(self) -> None:
        repo = _ValidatingInMemoryRepo()
        tenant_id = uuid4()
        ev1 = _make_event(tenant_id=tenant_id, sequence_no=1, previous_hash=None)
        await repo.append_existing_event(ev1)

        ev_dup = _make_event(tenant_id=tenant_id, sequence_no=1, previous_hash=ev1.event_hash)
        with pytest.raises(ValueError, match="sequence_no"):
            await repo.append_existing_event(ev_dup)

    async def test_rejects_sequence_no_gap(self) -> None:
        repo = _ValidatingInMemoryRepo()
        tenant_id = uuid4()
        ev1 = _make_event(tenant_id=tenant_id, sequence_no=1, previous_hash=None)
        await repo.append_existing_event(ev1)

        ev_gap = _make_event(tenant_id=tenant_id, sequence_no=3, previous_hash=ev1.event_hash)
        with pytest.raises(ValueError, match="sequence_no"):
            await repo.append_existing_event(ev_gap)

    async def test_rejects_sequence_no_before_chain_start(self) -> None:
        repo = _ValidatingInMemoryRepo()
        tenant_id = uuid4()
        ev_zero = _make_event(tenant_id=tenant_id, sequence_no=0, previous_hash=None)
        with pytest.raises(ValueError, match="sequence_no"):
            await repo.append_existing_event(ev_zero)


class TestNegativeHashChainIntegrity:
    """event_hash가 재계산값과 다르면(변조) 거부되어야 한다(AUD-003)."""

    async def test_rejects_tampered_event_hash(self) -> None:
        from dataclasses import replace

        repo = _ValidatingInMemoryRepo()
        tenant_id = uuid4()
        ev = _make_event(tenant_id=tenant_id, sequence_no=1, previous_hash=None)
        tampered = replace(ev, event_hash="0" * 64)

        with pytest.raises(ValueError, match="event_hash mismatch"):
            await repo.append_existing_event(tampered)

    async def test_rejects_event_with_wrong_previous_hash(self) -> None:
        """previous_hash가 실제 이전 이벤트의 event_hash와 다르면(체인 포크
        시도) 거부되어야 한다."""
        repo = _ValidatingInMemoryRepo()
        tenant_id = uuid4()
        ev1 = _make_event(tenant_id=tenant_id, sequence_no=1, previous_hash=None)
        await repo.append_existing_event(ev1)

        forked = _make_event(
            tenant_id=tenant_id, sequence_no=2, previous_hash="not-the-real-previous-hash"
        )
        with pytest.raises(ValueError, match="event_hash mismatch"):
            await repo.append_existing_event(forked)


class TestNegativeUnsafePayload:
    """§2: secret/token 류 키 이름을 담은 payload는 append 전에 거부되어야 한다."""

    async def test_append_event_rejects_unsafe_top_level_key(self) -> None:
        repo = _ValidatingInMemoryRepo()
        with pytest.raises(UnsafePayloadError):
            await repo.append_event(
                tenant_id=uuid4(),
                aggregate_type="mandate",
                aggregate_id=uuid4(),
                aggregate_revision=1,
                action="activate",
                outcome=Outcome.SUCCESS,
                actor_subject_id=uuid4(),
                trace_id=uuid4(),
                payload={"api_key": "leaked"},
                classification=Classification.INTERNAL,
            )

    async def test_append_event_rejects_unsafe_nested_key(self) -> None:
        repo = _ValidatingInMemoryRepo()
        with pytest.raises(UnsafePayloadError):
            await repo.append_event(
                tenant_id=uuid4(),
                aggregate_type="mandate",
                aggregate_id=uuid4(),
                aggregate_revision=1,
                action="activate",
                outcome=Outcome.SUCCESS,
                actor_subject_id=uuid4(),
                trace_id=uuid4(),
                payload={"detail": {"password": "hunter2"}},
                classification=Classification.INTERNAL,
            )


# ── failure injection (>=1) ──────────────────────────────────────────────────


class TestFailureInjection:
    """의존성 예외가 포트 경계에서 삼켜지지 않고 그대로 전파되는지 확인한다."""

    async def test_append_event_connection_error_propagates_uncaught(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _ValidatingInMemoryRepo()

        async def _raise_connection_error(**_: Any) -> AuditEvent:
            raise ConnectionError("mock: advisory lock acquisition failed")

        monkeypatch.setattr(repo, "append_event", _raise_connection_error)

        with pytest.raises(ConnectionError, match="advisory lock"):
            await repo.append_event(
                tenant_id=uuid4(),
                aggregate_type="mandate",
                aggregate_id=uuid4(),
                aggregate_revision=1,
                action="activate",
                outcome=Outcome.SUCCESS,
                actor_subject_id=uuid4(),
                trace_id=uuid4(),
                payload={},
                classification=Classification.INTERNAL,
            )

    async def test_list_chain_for_verification_timeout_propagates_uncaught(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AUD-003 검증 경로가 쿼리 타임아웃을 조용히 빈 리스트로 바꿔치기하지
        않는지 — 체인 검증이 '검증 안 함'으로 둔갑하면 변조를 못 잡는다."""
        repo = _ValidatingInMemoryRepo()

        async def _raise_timeout(*_: Any, **__: Any) -> list[AuditEvent]:
            raise TimeoutError("mock: query timeout")

        monkeypatch.setattr(repo, "list_chain_for_verification", _raise_timeout)

        with pytest.raises(TimeoutError, match="query timeout"):
            await repo.list_chain_for_verification(tenant_id=None)


# ── positive control (tenant chains stay independent, Rule 79 §1) ───────────


class TestChainTenantScoping:
    async def test_system_and_tenant_chains_both_allow_sequence_no_one(self) -> None:
        repo = _ValidatingInMemoryRepo()
        tenant_id = uuid4()

        system_event = await repo.append_event(
            tenant_id=None,
            aggregate_type="system",
            aggregate_id=uuid4(),
            aggregate_revision=None,
            action="boot",
            outcome=Outcome.SUCCESS,
            actor_subject_id=None,
            trace_id=uuid4(),
            payload={},
            classification=Classification.INTERNAL,
        )
        tenant_event = await repo.append_event(
            tenant_id=tenant_id,
            aggregate_type="mandate",
            aggregate_id=uuid4(),
            aggregate_revision=1,
            action="activate",
            outcome=Outcome.SUCCESS,
            actor_subject_id=uuid4(),
            trace_id=uuid4(),
            payload={},
            classification=Classification.INTERNAL,
        )

        assert system_event.sequence_no == 1
        assert tenant_event.sequence_no == 1
        assert system_event.tenant_id is None
        assert tenant_event.tenant_id == tenant_id
