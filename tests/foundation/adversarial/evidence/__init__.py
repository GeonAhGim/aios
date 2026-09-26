"""Audit Evidence adversarial 테스트 — 79번 §1(해시 체인)/§2(payload 안전성)
순수 규칙 함수(`src/foundation/evidence/domain/rules.py`)에 대한 negative/
실패주입 케이스. DB 없이 도는 단위 테스트라 이 패키지 `__init__.py`에 직접
둔다(task-7928 DEEPEN, 원 리프 task-6704)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome
from src.foundation.evidence.domain.rules import (
    ChainIntegrityError,
    UnsafePayloadError,
    assert_safe_payload,
    compute_event_hash,
    compute_payload_hash,
    verify_chain,
)


def _make_event(
    *,
    sequence_no: int,
    previous_hash: str | None,
    event_hash: str | None = None,
    occurred_at: datetime | None,
    tenant_id=None,
) -> AuditEvent:
    aggregate_id = uuid4()
    payload_hash = compute_payload_hash({})
    if event_hash is None and occurred_at is not None:
        event_hash = compute_event_hash(
            previous_hash=previous_hash,
            tenant_id=tenant_id,
            sequence_no=sequence_no,
            aggregate_type="mandate_revision",
            aggregate_id=aggregate_id,
            action="mandate_activated",
            outcome=Outcome.SUCCESS,
            payload_hash=payload_hash,
            classification=Classification.INTERNAL,
            occurred_at=occurred_at,
        )
    return AuditEvent(
        id=uuid4(),
        tenant_id=tenant_id,
        sequence_no=sequence_no,
        aggregate_type="mandate_revision",
        aggregate_id=aggregate_id,
        aggregate_revision=None,
        action="mandate_activated",
        outcome=Outcome.SUCCESS,
        actor_subject_id=tenant_id,
        trace_id=uuid4(),
        payload_hash=payload_hash,
        payload={},
        classification=Classification.INTERNAL,
        previous_hash=previous_hash,
        event_hash=event_hash or "",
        occurred_at=occurred_at,
    )


# --- negative tests (AUD-004 payload safety) --------------------------------


def test_assert_safe_payload_rejects_top_level_secret_key():
    with pytest.raises(UnsafePayloadError):
        assert_safe_payload({"api_key": "abc123"})


def test_assert_safe_payload_rejects_nested_token_key():
    with pytest.raises(UnsafePayloadError):
        assert_safe_payload({"meta": {"nested": {"access_token": "abc123"}}})


def test_assert_safe_payload_rejects_case_insensitive_password_key():
    with pytest.raises(UnsafePayloadError):
        assert_safe_payload({"UserPassword": "hunter2"})


# --- negative tests (AUD-003 chain integrity) --------------------------------


def test_verify_chain_rejects_broken_previous_hash_link():
    now = datetime.now(timezone.utc)
    first = _make_event(sequence_no=1, previous_hash=None, occurred_at=now)
    # sequence_no=2의 previous_hash가 first.event_hash가 아니라 임의 값 — 체인 단절.
    second = _make_event(sequence_no=2, previous_hash="not-the-real-previous-hash", occurred_at=now)

    with pytest.raises(ChainIntegrityError):
        verify_chain([first, second])


def test_verify_chain_rejects_event_missing_occurred_at():
    now = datetime.now(timezone.utc)
    first = _make_event(sequence_no=1, previous_hash=None, occurred_at=now)
    corrupted = _make_event(
        sequence_no=2,
        previous_hash=first.event_hash,
        occurred_at=now,
    )
    # occurred_at을 사후에 지워 재해시가 불가능한 상태를 시뮬레이션한다.
    corrupted = AuditEvent(**{**corrupted.__dict__, "occurred_at": None})

    with pytest.raises(ChainIntegrityError):
        verify_chain([first, corrupted])


def test_verify_chain_rejects_tampered_event_content():
    """event_hash는 그대로 두고 action만 바꾼 변조 시나리오 — 필드로부터
    재계산한 해시가 저장된 event_hash와 달라야 한다."""
    now = datetime.now(timezone.utc)
    first = _make_event(sequence_no=1, previous_hash=None, occurred_at=now)
    tampered = AuditEvent(**{**first.__dict__, "action": "mandate_deactivated"})

    with pytest.raises(ChainIntegrityError):
        verify_chain([tampered])


# --- failure injection -------------------------------------------------------


def test_compute_event_hash_propagates_hashing_backend_failure(monkeypatch):
    """의존 라이브러리(hashlib.sha256)가 예외를 던지면 해시를 조용히
    성공시키지 않고 그대로 전파해야 한다(fail-closed) — 79번 §4."""
    import src.foundation.evidence.domain.rules as rules_module

    def _boom(*args, **kwargs):
        raise RuntimeError("injected hashlib failure")

    monkeypatch.setattr(rules_module.hashlib, "sha256", _boom)

    with pytest.raises(RuntimeError, match="injected hashlib failure"):
        compute_event_hash(
            previous_hash=None,
            tenant_id=None,
            sequence_no=1,
            aggregate_type="mandate_revision",
            aggregate_id=uuid4(),
            action="mandate_activated",
            outcome=Outcome.SUCCESS,
            payload_hash="deadbeef",
            classification=Classification.INTERNAL,
            occurred_at=datetime.now(timezone.utc),
        )


# --- performance assertion ---------------------------------------------------


@pytest.mark.perf
def test_verify_chain_p95_latency_budget_for_1000_events():
    """1,000개 이벤트 체인 검증이 p95 100ms 예산 안에 들어야 한다(순수 CPU
    연산, I/O 없음 — ADR-2026-09-09-C 성능 예산표 기준 로컬 상한)."""
    now = datetime.now(timezone.utc)
    events: list[AuditEvent] = []
    previous_hash: str | None = None
    for seq in range(1, 1001):
        event = _make_event(sequence_no=seq, previous_hash=previous_hash, occurred_at=now)
        events.append(event)
        previous_hash = event.event_hash

    samples = []
    for _ in range(5):
        start = time.perf_counter()
        verify_chain(events)
        samples.append(time.perf_counter() - start)

    samples.sort()
    p95 = samples[-1]
    assert p95 < 0.1, f"verify_chain p95={p95:.4f}s exceeds 100ms budget for 1000 events"
