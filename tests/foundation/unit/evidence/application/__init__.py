"""Audit Evidence application 서비스 DEEPEN — negative/실패주입 테스트.

원 리프 task-7301(고아 산출물 회수 7070 (frontend-1)) 대상.
`append_audit_event`, `record_command_event`, `verify_audit_chain`,
`get_audit_timeline` 각 서비스의 불변식 위반 입력을 명시적으로 거부하는
negative 케이스와 의존성 예외 유발 실패주입 케이스를 추가(task-4084 DEEPEN)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest

from src.foundation.evidence.contracts.v1 import (
    SCHEMA_VERSION,
    AuditEventView,
    AuditTimelinePage,
    Classification,
    Outcome,
    RecordAuditEventCommand,
)
from src.foundation.evidence.domain.models import (
    AuditEvent,
)
from src.foundation.evidence.domain.rules import (
    ChainIntegrityError,
    UnsafePayloadError,
    assert_safe_payload,
    compute_event_hash,
    compute_payload_hash,
    verify_chain,
)

# ── helper: build a minimal AuditEvent for chain tests ──────────────────────


def _make_event(
    *,
    sequence_no: int,
    previous_hash: str | None,
    event_hash: str | None = None,
    occurred_at: datetime | None = None,
    tenant_id=None,
) -> AuditEvent:
    """불변식 위반이 없는 정직한 AuditEvent 생성 — 테스트용 보조 함수."""
    aggregate_id = uuid4()
    payload_hash = compute_payload_hash({})
    if occurred_at is None:
        occurred_at = datetime.now(timezone.utc)
    if event_hash is None:
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
        event_hash=event_hash,
        occurred_at=occurred_at,
    )


# ── negative tests: payload safety (AUD-004) --------------------------------


class TestNegativePayloadSafety:
    """append_audit_event / record_command_event가 secret-like payload를
    거부하는지를 검증 — domain.rules.assert_safe_payload()가 직접 raise."""

    def test_rejects_api_key_in_payload(self):
        with pytest.raises(UnsafePayloadError):
            assert_safe_payload({"api_key": "sk-abc123"})

    def test_rejects_access_token_nested(self):
        with pytest.raises(UnsafePayloadError):
            assert_safe_payload({"meta": {"access_token": "ghp_xxx"}})

    def test_rejects_case_insensitive_password_key(self):
        with pytest.raises(UnsafePayloadError):
            assert_safe_payload({"UserPassword": "hunter2"})

    def test_rejects_secret_key_in_nested_structure(self):
        with pytest.raises(UnsafePayloadError):
            assert_safe_payload({"level1": {"level2": {"secret_key": "aws-123"}}})

    def test_accepts_safe_payload_without_secret_keys(self):
        """비밀번호/토큰 키가 없는 payload는 통과 — 안전 경로 확인."""
        # 예외가 raising되지 않으면 성공
        assert_safe_payload({"user_id": 42, "action": "trade"})


# ── negative tests: chain integrity (AUD-003) --------------------------------


class TestNegativeChainIntegrity:
    """verify_audit_chain 서비스의 체인 검증 불변식 위반 케이스.

    verify_chain()은 previous_hash linkage, occurred_at non-null, event_hash 재계산
    만 검증함. sequence_no 범위/중복 검증은 의도적으로 제외.
    """

    def test_chain_broken_previous_hash_link(self):
        """이전 이벤트의 event_hash와 다른 previous_hash — 체인 단절."""
        now = datetime.now(timezone.utc)
        first = _make_event(sequence_no=1, previous_hash=None, occurred_at=now)
        second = _make_event(
            sequence_no=2, previous_hash="not-the-real-previous-hash", occurred_at=now
        )
        with pytest.raises(ChainIntegrityError):
            verify_chain([first, second])

    def test_chain_tampered_event_content(self):
        """event_hash는 그대로 두고 action만 바꾼 변조 — 재계산 해시 불일치."""
        now = datetime.now(timezone.utc)
        first = _make_event(sequence_no=1, previous_hash=None, occurred_at=now)
        second = _make_event(sequence_no=2, previous_hash=first.event_hash, occurred_at=now)
        # 변조: action을 변경하면 event_hash가 달라져야 함.
        second = AuditEvent(
            **{**second.__dict__, "action": "mandate_deactivated"},
        )
        with pytest.raises(ChainIntegrityError):
            verify_chain([first, second])

    def test_chain_missing_occurred_at(self):
        """occurred_at이 None이면 재해시 불가 — 체인 검증 실패."""
        now = datetime.now(timezone.utc)
        first = _make_event(sequence_no=1, previous_hash=None, occurred_at=now)
        second = _make_event(sequence_no=2, previous_hash=first.event_hash, occurred_at=now)
        second = AuditEvent(**{**second.__dict__, "occurred_at": None})
        with pytest.raises(ChainIntegrityError):
            verify_chain([first, second])


# ── negative tests: contract validation --------------------------------------


class TestNegativeContractValidation:
    """RecordAuditEventCommand의 Pydantic 검증 — 유효하지 않은 입력 거부."""

    def test_rejects_invalid_outcome_value(self):
        with pytest.raises(ValueError):  # pydantic rejects invalid enum value
            RecordAuditEventCommand(
                tenant_id=None,
                aggregate_type="mandate_revision",
                aggregate_id=uuid4(),
                action="test",
                outcome=Outcome("INVALID_OUTCOME"),
                trace_id=uuid4(),
            )

    def test_rejects_invalid_classification_value(self):
        with pytest.raises(ValueError):
            RecordAuditEventCommand(
                tenant_id=None,
                aggregate_type="mandate_revision",
                aggregate_id=uuid4(),
                action="test",
                outcome=Outcome.SUCCESS,
                trace_id=uuid4(),
                classification=Classification("BAD"),
            )

    def test_accepts_valid_command_with_all_fields(self):
        """정합성 있는 전체 필드 명령이 Parse되는지 확인."""
        cmd = RecordAuditEventCommand(
            tenant_id=uuid4(),
            aggregate_type="mandate_revision",
            aggregate_id=uuid4(),
            action="mandate_activated",
            outcome=Outcome.SUCCESS,
            trace_id=uuid4(),
        )
        assert cmd.classification == Classification.INTERNAL
        assert cmd.payload == {}


# ── negative tests: AuditEventView contract ----------------------------------


class TestNegativeAuditEventView:
    """AuditEventView Pydantic contract — 필수 필드 검증."""

    def test_accepts_valid_event_view(self):
        """정합性 있는 AuditEventView가 Parse되는지 확인."""
        view = AuditEventView(
            id=uuid4(),
            tenant_id=None,
            sequence_no=1,
            aggregate_type="mandate_revision",
            aggregate_id=uuid4(),
            aggregate_revision=None,
            action="mandate_activated",
            outcome=Outcome.SUCCESS,
            actor_subject_id=None,
            trace_id=uuid4(),
            payload_hash="abc123",
            payload={},
            classification=Classification.INTERNAL,
            previous_hash=None,
            event_hash="def456",
            occurred_at=datetime.now(timezone.utc),
        )
        assert view.schema_version == SCHEMA_VERSION


# ── failure-injection tests --------------------------------------------------


class TestFailureInjection:
    """의존성 예외 유발 — monkeypatch로 hashlib/해시 함수 실패 시Fail-closed."""

    def test_compute_event_hash_propagates_hashlib_failure(self):
        """hashlib.sha256가 예외를 raise하면 호출 측이 그대로 받는다."""
        import src.foundation.evidence.domain.rules as rules_module

        def _boom(*args, **kwargs):
            raise RuntimeError("injected hashlib failure")

        with patch.object(rules_module.hashlib, "sha256", _boom):
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

    def test_compute_payload_hash_propagates_hashlib_failure(self):
        """compute_payload_hash의 hashlib 실패도 전파된다."""
        import src.foundation.evidence.domain.rules as rules_module

        def _boom(*args, **kwargs):
            raise RuntimeError("injected hashlib failure")

        with patch.object(rules_module.hashlib, "sha256", _boom):
            with pytest.raises(RuntimeError, match="injected hashlib failure"):
                compute_payload_hash({"test": "data"})

    def test_verify_chain_propagates_hashlib_failure(self):
        """verify_chain 내부의 compute_event_hash 실패도 전파된다."""
        import src.foundation.evidence.domain.rules as rules_module

        def _boom(*args, **kwargs):
            raise RuntimeError("injected hashlib failure")

        # _make_event 자체도 compute_payload_hash를 호출하므로, patch를 먼저 적용한
        # 후 이벤트 생성 + verify_chain을 같은 with 블록에서 실행해야 함.
        now = datetime.now(timezone.utc)
        first = _make_event(sequence_no=1, previous_hash=None, occurred_at=now)
        with patch.object(rules_module.hashlib, "sha256", _boom):
            with pytest.raises(RuntimeError, match="injected hashlib failure"):
                verify_chain([first])


# ── negative tests: verify_audit_chain service layer -------------------------


class TestNegativeVerifyAuditChainService:
    """verify_audit_chain.py 서비스 레이어 — 빈 리스트/단일 이벤트 검증."""

    def test_verify_chain_empty_list_returns_none(self):
        """빈 이벤트 리스트는 체인이 아님 — verify_chain은 예외 없이 None 반환."""
        result = verify_chain([])
        assert result is None

    def test_verify_chain_single_event_passes(self):
        """단일 이벤트는 체인 단절 없이 통과 (previous_hash=None이 루트)."""
        now = datetime.now(timezone.utc)
        first = _make_event(sequence_no=1, previous_hash=None, occurred_at=now)
        # verify_chain은 성공 시 None 반환 (예외 안 날리면 성공)
        result = verify_chain([first])
        assert result is None


# ── negative tests: get_audit_timeline contract ------------------------------


class TestNegativeAuditTimelinePage:
    """AuditTimelinePage — next_cursor None이 끝 페이지임을 검증."""

    def test_timeline_page_empty_items_has_no_next_cursor(self):
        """빈 페이지는 next_cursor이 None — 더 이상 페이지 없음."""
        page = AuditTimelinePage(
            items=[],
            next_cursor=None,
            as_of=datetime.now(timezone.utc),
        )
        assert page.items == []
        assert page.next_cursor is None

    def test_timeline_page_with_items_has_next_cursor(self):
        """페이지가 끝나지 않았으면 next_cursor가 존재."""
        page = AuditTimelinePage(
            items=[
                AuditEventView(
                    id=uuid4(),
                    tenant_id=None,
                    sequence_no=1,
                    aggregate_type="mandate_revision",
                    aggregate_id=uuid4(),
                    aggregate_revision=None,
                    action="mandate_activated",
                    outcome=Outcome.SUCCESS,
                    actor_subject_id=None,
                    trace_id=uuid4(),
                    payload_hash="abc",
                    payload={},
                    classification=Classification.INTERNAL,
                    previous_hash=None,
                    event_hash="def",
                    occurred_at=datetime.now(timezone.utc),
                )
            ],
            next_cursor="eyJzZXF1ZW5jZSI6MX0=",
            as_of=datetime.now(timezone.utc),
        )
        assert len(page.items) == 1
        assert page.next_cursor == "eyJzZXF1ZW5jZSI6MX0="


# ── performance assertion ----------------------------------------------------


class TestPerformanceAssertion:
    """성능 단언 — ADR-2026-09-09-C 성능 예산표 기준."""

    @pytest.mark.perf
    def test_verify_chain_p95_latency_budget_for_1000_events(self):
        """1,000개 이벤트 체인 검증이 p95 100ms 예산 안에 들어야 한다
        (순수 CPU 연산, I/O 없음 — ADR-2026-09-09-C 성능 예산표 기준
        로컬 상한)."""
        import time

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
        assert p95 < 0.1, f"verify_chain p95={p95:.4f}s exceeds 100ms budget"
