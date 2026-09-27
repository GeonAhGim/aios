"""Audit Evidence 패키지 경계 테스트 — task-8391 DEEPEN(원 리프 task-6704).

`tests/foundation/unit/evidence/test_rules.py`가 이미 해시체인/payload 안전성의
핵심 시나리오를 덮는다. 여기서는 그 파일이 다루지 않는 두 축을 추가한다:
1) 도메인 모델(`AuditEvent`/`Outcome`/`Classification`)의 타입·불변식 위반 입력
   거부, 2) `compute_payload_hash`의 직렬화 의존성 실패 전파. DB 없이 도는 순수
   단위 테스트라 (allocation/evidence-adversarial 선례처럼) 패키지 `__init__.py`
   에 직접 둔다."""

from __future__ import annotations

import dataclasses
import time
from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

import pytest

from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome
from src.foundation.evidence.domain.rules import compute_event_hash, compute_payload_hash

NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)


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
    return cast(Any, AuditEvent)(**defaults)


# --- negative tests (모델 불변식 위반 입력 거부) ------------------------------


def test_audit_event_is_frozen_and_rejects_field_mutation():
    """79번 §1: audit_event는 append-only다 — 이미 만든 이벤트를 코드가 실수로
    제자리 수정하려 하면 즉시 막혀야 한다(WORM 우회 방지)."""
    event = _event()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cast(Any, event).action = "mandate_deactivated"


def test_outcome_enum_rejects_unknown_value():
    """DB나 외부 입력에서 온 값이 79번 §1이 정의한 SUCCESS/DENIED/ERROR 밖이면
    조용히 통과시키지 않고 명시적으로 거부한다."""
    with pytest.raises(ValueError):
        Outcome("PARTIALLY_SUCCESSFUL")


def test_classification_enum_rejects_unknown_value():
    """분류 값도 같은 이유로 닫힌 집합이어야 한다 — 임의 문자열이 들어오면
    거부되어야 컴플라이언스 분류가 조용히 깨지지 않는다."""
    with pytest.raises(ValueError):
        Classification("TOP_SECRET")


def test_compute_event_hash_rejects_outcome_missing_value_attribute():
    """`outcome`은 반드시 `Outcome` 열거형이어야 한다 — 호출자가 실수로 평범한
    문자열을 넘기면 `.value` 접근에서 즉시 실패해야 한다(위조된 문자열을
    해시 체인에 조용히 섞어 넣는 경로를 막는다)."""
    with pytest.raises(AttributeError):
        compute_event_hash(
            previous_hash=None,
            tenant_id=None,
            sequence_no=1,
            aggregate_type="mandate_revision",
            aggregate_id=uuid4(),
            action="mandate_activated",
            outcome=cast(Any, "SUCCESS"),  # Outcome이 아닌 평범한 str
            payload_hash="deadbeef",
            classification=Classification.INTERNAL,
            occurred_at=NOW,
        )


# --- 실패주입 (직렬화 의존성 장애 전파) --------------------------------------


def test_compute_payload_hash_propagates_json_serialization_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`json.dumps`가 실패하면(예: 순환 참조·직렬화 불가 객체) payload_hash를
    조작해 성공으로 위장하지 않고 예외를 그대로 전파해야 한다(fail-closed)."""
    import src.foundation.evidence.domain.rules as rules_module

    def _boom(*args: object, **kwargs: object) -> str:
        raise TypeError("injected json serialization failure")

    monkeypatch.setattr(rules_module.json, "dumps", _boom)

    with pytest.raises(TypeError, match="injected json serialization failure"):
        compute_payload_hash({"purpose": "trading_risk"})


# --- 성능 단언 -----------------------------------------------------------------


@pytest.mark.perf
def test_compute_payload_hash_throughput_budget():
    """D2 성능 단언 — 순수 CPU 해시 연산이므로 5,000회 호출이 500ms 예산(=
    10k ops/sec 이상) 안에 들어야 한다. 회귀 시(예: 매 호출마다 불필요한 딥카피
    추가) 여기서 잡힌다."""
    payload = {"purpose": "trading_risk", "revision": 1, "nested": {"a": 1, "b": 2}}

    started = time.perf_counter()
    for _ in range(5000):
        compute_payload_hash(payload)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5, f"compute_payload_hash 5000회 처리 {elapsed:.4f}s가 500ms 예산 초과"
