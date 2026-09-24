"""AuditEvent/Outcome/Classification 값 객체 단위테스트 — DB/HTTP 없이 순수
데이터클래스 계약(불변성, 기본값 격리, 잘못된 입력 거부)만 검증한다.

Spec: AIOSproject 79_audit_evidence_l3_build_and_operational_specification_v1.0.md §1.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


def _event(**overrides: object) -> AuditEvent:
    defaults: dict[str, Any] = dict(
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
        occurred_at=NOW,
    )
    defaults.update(overrides)
    return AuditEvent(**defaults)


def test_construct_with_defaults():
    event = _event()
    assert event.payload == {}
    assert event.classification == Classification.INTERNAL
    assert event.previous_hash is None
    assert event.event_hash == ""


def test_system_event_allows_none_tenant_id():
    event = _event(tenant_id=None)
    assert event.tenant_id is None


def test_payload_default_factory_is_isolated_per_instance():
    """실패주입: default_factory가 아니라 공유 mutable 기본값이었다면 한
    인스턴스의 payload를 건드리는 순간 다른 인스턴스까지 오염됐을 것이다."""
    first = _event()
    second = _event()
    first.payload["leaked"] = True
    assert "leaked" not in second.payload


def test_event_is_frozen_negative():
    """negative: append-only 감사 이벤트는 생성 후 필드 재할당이 금지된다."""
    event = _event()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(event, "action", "tampered")  # noqa: B010 -- must bypass mypy's frozen-assignment check, not the runtime one


def test_missing_required_field_raises_negative():
    """negative: 필수 필드(payload_hash 등) 누락 시 TypeError로 즉시 실패한다."""
    kwargs: dict[str, Any] = dict(
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
    )
    with pytest.raises(TypeError):
        AuditEvent(**kwargs)


def test_invalid_outcome_value_raises_negative():
    """negative: Outcome은 SUCCESS/DENIED/ERROR 세 값만 허용한다."""
    with pytest.raises(ValueError):
        Outcome("NOT_A_REAL_OUTCOME")


def test_invalid_classification_value_raises_negative():
    """negative: Classification enum도 정의되지 않은 값은 거부한다."""
    with pytest.raises(ValueError):
        Classification("TOP_SECRET")


@pytest.mark.parametrize(
    "classification",
    [
        Classification.PUBLIC,
        Classification.INTERNAL,
        Classification.CONFIDENTIAL,
        Classification.RESTRICTED,
        Classification.SECRET_REFERENCE,
    ],
)
def test_all_classification_members_constructible(classification):
    event = _event(classification=classification)
    assert event.classification is classification


def test_outcome_and_classification_are_str_enums():
    """79 §1 계약: Outcome/Classification은 JSON 직렬화 경계에서 plain str처럼
    비교 가능해야 한다(감사 로그 payload에 그대로 실린다)."""
    assert Outcome.SUCCESS == "SUCCESS"
    assert Classification.RESTRICTED == "RESTRICTED"
