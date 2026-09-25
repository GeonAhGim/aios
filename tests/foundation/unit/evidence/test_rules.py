"""79번 §1/§2 규칙의 단위테스트 — DB 없이 순수 함수만 검증한다."""
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

NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)


def test_safe_payload_passes():
    assert_safe_payload({"purpose": "trading_risk", "revision": 1})


@pytest.mark.parametrize(
    "key", ["password", "api_key", "access_token", "PRIVATE_KEY", "user_credential"]
)
def test_unsafe_payload_key_rejected(key):
    with pytest.raises(UnsafePayloadError):
        assert_safe_payload({key: "whatever"})


def test_unsafe_payload_key_rejected_when_nested():
    with pytest.raises(UnsafePayloadError):
        assert_safe_payload({"outer": {"secret_ref": "x"}})


def test_payload_hash_is_order_independent():
    assert compute_payload_hash({"a": 1, "b": 2}) == compute_payload_hash({"b": 2, "a": 1})


def test_payload_hash_changes_with_content():
    assert compute_payload_hash({"a": 1}) != compute_payload_hash({"a": 2})


def _event(**overrides: object) -> AuditEvent:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        sequence_no=1,
        aggregate_type="mandate_revision",
        aggregate_id=uuid4(),
        aggregate_revision=1,
        action="mandate_activated",
        outcome=Outcome.SUCCESS,
        actor_subject_id=uuid4(),
        trace_id=uuid4(),
        payload_hash="hash",
        payload={},
        classification=Classification.INTERNAL,
        previous_hash=None,
        occurred_at=NOW,
    )
    defaults.update(overrides)
    event = AuditEvent(**defaults)  # type: ignore[arg-type]
    computed = compute_event_hash(
        previous_hash=event.previous_hash,
        tenant_id=event.tenant_id,
        sequence_no=event.sequence_no,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        action=event.action,
        outcome=event.outcome,
        payload_hash=event.payload_hash,
        classification=event.classification,
        occurred_at=event.occurred_at,
    )
    return AuditEvent(**{**defaults, "event_hash": computed})  # type: ignore[arg-type]


def test_single_event_chain_verifies():
    verify_chain([_event()])


def test_two_linked_events_verify():
    first = _event(sequence_no=1)
    second = _event(sequence_no=2, previous_hash=first.event_hash)
    verify_chain([first, second])


def test_broken_link_is_detected():
    first = _event(sequence_no=1)
    second = _event(sequence_no=2, previous_hash="wrong-hash")
    with pytest.raises(ChainIntegrityError):
        verify_chain([first, second])


def test_tampered_payload_hash_is_detected():
    """이벤트 저장 후 payload_hash만 몰래 바꾼 상황을 재현 — event_hash는
    원래 값 그대로라 재계산 결과와 어긋난다."""
    event = _event()
    tampered = AuditEvent(**{**event.__dict__, "payload_hash": "tampered"})
    with pytest.raises(ChainIntegrityError):
        verify_chain([tampered])


def test_missing_occurred_at_breaks_chain():
    """occurred_at이 없으면(저장 계층 결함으로 NULL이 흘러든 경우) 해시를
    재계산할 수 없으니 조용히 통과시키지 않고 명시적으로 체인 단절 처리한다."""
    event = _event()
    broken = AuditEvent(**{**event.__dict__, "occurred_at": None})
    with pytest.raises(ChainIntegrityError):
        verify_chain([broken])


def test_missing_middle_event_breaks_chain():
    """AUD-003 — 중간 이벤트가 통째로 삭제되면(WORM을 우회한 경우) 다음
    이벤트의 previous_hash가 그 앞의 실제 event_hash와 안 맞아 걸린다."""
    first = _event(sequence_no=1)
    second = _event(sequence_no=2, previous_hash=first.event_hash)
    third = _event(sequence_no=3, previous_hash=second.event_hash)
    with pytest.raises(ChainIntegrityError):
        verify_chain([first, third])  # second가 삭제된 상황


@pytest.mark.perf
def test_verify_chain_throughput_budget():
    """D2 성능 단언 — 79번 스펙에 축별 예산표가 아직 없는 신규 모듈이라, 5k
    이벤트/500ms(=10k events/sec 이상 처리량)를 로컬 기준으로 고정한다. 순수
    해시 계산이므로 CI 러너에서도 여유 있게 통과해야 하고, 회귀 시(예: 매
    이벤트마다 불필요한 재직렬화 추가) 여기서 잡힌다."""
    events: list[AuditEvent] = []
    previous_hash: str | None = None
    for sequence_no in range(1, 5001):
        event = _event(sequence_no=sequence_no, previous_hash=previous_hash)
        events.append(event)
        previous_hash = event.event_hash

    started = time.perf_counter()
    verify_chain(events)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5


def test_replayed_stale_event_breaks_chain_red_gate_repro():
    """게이트 적색 재현 — AUD-003의 실제 공격 시나리오: 오래된(이미 체인에
    포함된) 이벤트를 뒤쪽 sequence_no로 재주입해 감사 로그를 부풀리거나
    최신 이벤트를 가리는 replay 공격. previous_hash가 실제로 그 자리에
    와야 할 직전 이벤트의 event_hash와 다르므로 verify_chain이 이 조작을
    막아야 한다 — 79번 §4 `INTEGRITY_AUDIT_CHAIN_BROKEN`로 매핑되는 경로."""
    first = _event(sequence_no=1)
    second = _event(sequence_no=2, previous_hash=first.event_hash)
    replayed_first_as_third = _event(sequence_no=3, previous_hash=first.event_hash)
    with pytest.raises(ChainIntegrityError):
        verify_chain([first, second, replayed_first_as_third])
