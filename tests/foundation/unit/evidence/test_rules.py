"""79번 §1/§2 규칙의 단위테스트 — DB 없이 순수 함수만 검증한다."""

from datetime import UTC, datetime
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
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import virtual_delayed_sync

NOW = datetime(2026, 9, 2, tzinfo=UTC)


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
def test_verify_chain_throughput_budget(perf_budget):
    """D2 성능 단언 — 79번 스펙에 축별 예산표가 아직 없는 신규 모듈이라, 5k
    이벤트/500ms(=10k events/sec 이상 처리량)를 로컬 기준으로 고정한다. 순수
    해시 계산이므로 CI 러너에서도 여유 있게 통과해야 하고, 회귀 시(예: 매
    이벤트마다 불필요한 재직렬화 추가) 여기서 잡힌다.

    raw perf_counter → PerfBudget.assert_within 전환 (task-10974).
    """
    events: list[AuditEvent] = []
    previous_hash: str | None = None
    for sequence_no in range(1, 5001):
        event = _event(sequence_no=sequence_no, previous_hash=previous_hash)
        events.append(event)
        previous_hash = event.event_hash

    perf_budget.assert_within(
        lambda: verify_chain(events),
        budget_ms=500,
        n=5,
        label="verify_chain(5k events)",
    )


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


# ---- PERF-detection: 검출력 검증 (task-11539 감사 후속, task-16018) ----


@pytest.mark.perf
def test_verify_chain_budget_goes_red_with_cpu_injection(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """검출력 확인 — virtual_delayed_sync 로 CPU 시간을 주입하면 같은 단언이
    적색으로 넘어간다. 실제 sleep/busy-wait 를 쓰지 않고 ledger 에만 지연을
    더하므로 측정치에 지연이 추가되고 full 시간을 쓰지 않는다(10x 주입이
    테스트를 10배 느리게 만들지 않음).

    예산 500ms 로 실제 verify_chain(5k) 의 CPU 시간(~10-30ms 범위) 보다
    훨씬 크지만, 1초 가상 CPU 지연으로 측정값이 예산을 명확히 초과해야 한다.
    """
    events: list[AuditEvent] = []
    previous_hash: str | None = None
    for sequence_no in range(1, 5001):
        event = _event(sequence_no=sequence_no, previous_hash=previous_hash)
        events.append(event)
        previous_hash = event.event_hash

    # virtual_delayed_sync: verify_chain 호출에 1초 CPU+wall 지연을 ledger 에 추가
    delayed = virtual_delayed_sync(verify_chain, seconds=1.0)

    try:
        # 같은 단언(budget_ms=500); 가상 CPU 지연으로 반드시 적색이어야 함
        perf_budget.assert_within(
            lambda: delayed(events),
            budget_ms=500,
            n=5,
            label="verify_chain(5k events, cpu_injected)",
        )
        pytest.fail(
            "assert_within 이 적색을 raised 하지 않음 — CPU 주입이 감지되지 않음 (검출력 결함)"
        )
    except AssertionError as exc:
        # 적색이 감지됨 — 기대 동작
        assert "cpu=" in str(exc)


@pytest.mark.perf
def test_verify_chain_tight_budget_fails_proves_assertion_real(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """예산이 실제 측정값보다 작은 경우 assert_within 이 적색으로 넘어간다 —
    성능 단언이 '아무것도 잡지 못하는 허울'이 아님을 증명한다.

    5k 이벤트에 대해 0.001ms(1마이크로초) 예산은 현실적으로 불가능하므로
    반드시 적색이어야 한다. virtual_delayed_sync 으로 0.1ms 가상 CPU 지연을
    주입하면 실제 측정값(0ms 근처) + 지연 > 0.001ms 가 되어 적색이 보장된다.
    """
    events: list[AuditEvent] = []
    previous_hash: str | None = None
    for sequence_no in range(1, 5001):
        event = _event(sequence_no=sequence_no, previous_hash=previous_hash)
        events.append(event)
        previous_hash = event.event_hash

    # virtual_delayed_sync: 0.1ms 가상 CPU 지연 — 0.1ms > 예산 0.001ms
    delayed = virtual_delayed_sync(verify_chain, seconds=0.0001)

    with pytest.raises(AssertionError) as exc_info:
        perf_budget.assert_within(
            lambda: delayed(events),
            budget_ms=0.001,
            n=3,
            label="verify_chain(5k events, impossible_budget)",
        )
    # AssertionError 메시지에 측정값이 포함되어야 함
    assert "cpu=" in str(exc_info.value)
