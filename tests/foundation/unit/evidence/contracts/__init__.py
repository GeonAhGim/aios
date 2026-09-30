"""tests/foundation/unit/evidence/contracts/__init__.py -- evidence/contracts
패키지 cross-module 부정/실패주입/성능 테스트 (contracts/v1.py <-> domain/
models.py <-> application/append_audit_event.py 경계를 가로지르는 계약).

원 리프: task-6704 (고아 산출물 회수 5828 (qa-2))
DEEPEN 대상: task-4084 DEEPEN 기준 -- negative>=3, failure-injection>=1,
perf assertion>=1.

개별 모듈 단위 테스트는 test_v1.py(contracts)/test_models.py(domain)/
test_rules.py(domain)/test_append_audit_event.py(application)에 이미 두껍게
있다 -- 여기서는 그 모듈 경계를 넘나드는 계약, 특히 `contracts/v1.py`와
`domain/models.py`가 각자 독립적으로 정의하는 `Outcome`/`Classification`
enum이 "이름/값이 항상 같아야 한다"는 암묵적 불변식(evidence/contracts/v1.py
docstring "다른 바운디드 컨텍스트는 오직 이 파일을 통해서만" §4)에 집중한다.
이 불변식은 `append_audit_event.event_to_view()`/`append_audit_event()`가
`ContractOutcome(event.outcome.value)` / `DomainOutcome(command.outcome.value)`
로 매 요청마다 왕복 변환하는 근거이며, 두 enum이 한쪽만 갱신되어 드리프트
나면 이 변환이 `ValueError`로 깨진다 -- 조용히 통과시키면 안 되는 지점이다.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.foundation.evidence.application.append_audit_event import (
    append_audit_event,
    event_to_view,
)
from src.foundation.evidence.contracts.v1 import Classification as ContractClassification
from src.foundation.evidence.contracts.v1 import Outcome as ContractOutcome
from src.foundation.evidence.contracts.v1 import RecordAuditEventCommand
from src.foundation.evidence.domain.models import AuditEvent
from src.foundation.evidence.domain.models import Classification as DomainClassification
from src.foundation.evidence.domain.models import Outcome as DomainOutcome

NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)


def _event(**overrides: object) -> AuditEvent:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        sequence_no=1,
        aggregate_type="mandate_revision",
        aggregate_id=uuid4(),
        aggregate_revision=1,
        action="mandate_activated",
        outcome=DomainOutcome.SUCCESS,
        actor_subject_id=uuid4(),
        trace_id=uuid4(),
        payload_hash="hash",
        payload={},
        classification=DomainClassification.INTERNAL,
        previous_hash=None,
        event_hash="event-hash",
        occurred_at=NOW,
    )
    defaults.update(overrides)
    return AuditEvent(**defaults)  # type: ignore[arg-type]


def _command(**overrides: object) -> RecordAuditEventCommand:
    defaults: dict[str, object] = dict(
        tenant_id=uuid4(),
        aggregate_type="mandate_revision",
        aggregate_id=uuid4(),
        aggregate_revision=1,
        action="mandate_activated",
        outcome=ContractOutcome.SUCCESS,
        actor_subject_id=uuid4(),
        trace_id=uuid4(),
        payload={"purpose": "trading_risk"},
        classification=ContractClassification.INTERNAL,
    )
    defaults.update(overrides)
    return RecordAuditEventCommand(**defaults)


class _RogueMember:
    """도메인/계약 enum에 아직 짝이 없는 값을 흉내내는 스텁 -- 실제 enum
    멤버를 늘리지 않고도 "한쪽만 갱신된 드리프트" 상황을 재현한다."""

    def __init__(self, value: str) -> None:
        self.value = value


# ──────────────────────────────────────────────────────────────────────
# 1. Negative tests -- 모듈 경계를 넘는 불변식 위반 입력 거부
# ──────────────────────────────────────────────────────────────────────


class TestEnumParityInvariant:
    def test_negative_event_to_view_rejects_domain_outcome_absent_from_contract_enum(
        self,
    ) -> None:
        """부정: `domain/models.py::Outcome`에는 있지만 `contracts/v1.py::Outcome`
        에는 없는 값(예: 도메인만 먼저 갱신된 드리프트)이 이벤트에 실려 오면,
        `event_to_view`의 `ContractOutcome(event.outcome.value)` 왕복 변환이
        이를 유효한 값으로 오인해 통과시키지 않고 명시적으로 거부해야 한다."""
        drifted = _event(outcome=_RogueMember("ARCHIVED"))
        with pytest.raises(ValueError, match="ARCHIVED"):
            event_to_view(drifted)

    def test_negative_event_to_view_rejects_domain_classification_absent_from_contract_enum(
        self,
    ) -> None:
        """부정: Outcome과 동일한 드리프트 시나리오를 Classification에도
        적용 -- 두 enum 모두 왕복 변환 지점에서 같은 방어를 받는지 확인한다."""
        drifted = _event(classification=_RogueMember("TOP_SECRET"))
        with pytest.raises(ValueError, match="TOP_SECRET"):
            event_to_view(drifted)

    def test_negative_append_audit_event_rejects_contract_outcome_absent_from_domain_enum(
        self,
    ) -> None:
        """부정: 반대 방향 -- `contracts/v1.py::Outcome`에는 있지만
        `domain/models.py::Outcome`에는 없는 값(계약만 먼저 갱신된 드리프트)이
        커맨드에 실리면, `append_audit_event`의 `DomainOutcome(command.outcome.value)`
        변환이 이를 통과시키지 않고 거부해야 repo에 잘못된 이벤트가 쓰이지
        않는다."""
        command = _command()
        # pydantic validation을 우회해 "계약 enum에만 존재하는 드리프트 값"을
        # 직접 주입한다 -- 정상 생성 경로로는 재현 불가능한, enum이 갈라진
        # 상황 자체를 시뮬레이션하기 위함.
        drifted_command = command.model_copy(update={"outcome": _RogueMember("ARCHIVED")})

        class _UnreachableRepo:
            async def append_event(self, **kwargs: object) -> AuditEvent:
                raise AssertionError("도메인 enum 변환이 실패하면 repo까지 도달하면 안 된다")

        import asyncio

        with pytest.raises(ValueError, match="ARCHIVED"):
            asyncio.run(append_audit_event(_UnreachableRepo(), drifted_command))


# ──────────────────────────────────────────────────────────────────────
# 2. Failure-injection test -- 의존성 예외 유발
# ──────────────────────────────────────────────────────────────────────


class TestFailureInjection:
    def test_compute_payload_hash_failure_propagates_after_safety_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """실패주입: `assert_safe_payload`를 통과한 뒤 `compute_payload_hash`가
        (라이브러리 버그 등으로) 예외를 던지면, `append_audit_event`가 이를
        삼키고 repo에 부분 데이터를 쓰면 안 된다 -- fail-closed 전파."""
        import src.foundation.evidence.application.append_audit_event as append_module

        def _boom(payload: dict[str, object]) -> str:
            raise RuntimeError("injected compute_payload_hash dependency failure")

        monkeypatch.setattr(append_module, "compute_payload_hash", _boom)

        class _UnreachableRepo:
            async def append_event(self, **kwargs: object) -> AuditEvent:
                raise AssertionError("해시 계산이 실패하면 repo까지 도달하면 안 된다")

        import asyncio

        command = _command()
        with pytest.raises(RuntimeError, match="injected compute_payload_hash dependency failure"):
            asyncio.run(append_audit_event(_UnreachableRepo(), command))


# ──────────────────────────────────────────────────────────────────────
# 3. Performance assertion
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.perf
def test_perf_event_to_view_round_trip_enum_conversion() -> None:
    """성능 단언: `event_to_view`의 enum 왕복 변환(Outcome/Classification
    각 1회씩)을 포함한 전체 매핑이 1,000회 반복에서도 가벼운 순수 연산
    예산(0.2s) 안에 들어와야 한다 -- 이 변환이 hot append 경로(매 감사
    이벤트마다 실행)에 있기 때문에 회귀 시 여기서 잡힌다."""
    events = [_event(sequence_no=i, event_hash=f"hash-{i}") for i in range(1000)]

    started = time.perf_counter()
    for event in events:
        event_to_view(event)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.2


# ──────────────────────────────────────────────────────────────────────
# 4. Cross-module integration test -- enum 정합성이 실제로 유지됨을 증명
# ──────────────────────────────────────────────────────────────────────


def test_integration_all_contract_and_domain_outcome_members_round_trip() -> None:
    """통합: 드리프트가 없는 정상 상태에서는 `contracts/v1.py::Outcome`과
    `domain/models.py::Outcome`의 모든 멤버가 양방향으로 손실 없이
    왕복된다 -- 위 negative 테스트들이 시뮬레이션한 드리프트가 현재
    코드베이스에는 존재하지 않음을 실제로 증명한다."""
    assert {m.name for m in ContractOutcome} == {m.name for m in DomainOutcome}
    for member in ContractOutcome:
        assert DomainOutcome(member.value).value == member.value
    for member in DomainOutcome:
        assert ContractOutcome(member.value).value == member.value


def test_integration_all_contract_and_domain_classification_members_round_trip() -> None:
    """통합: Classification도 동일하게 드리프트 없음을 증명한다."""
    assert {m.name for m in ContractClassification} == {m.name for m in DomainClassification}
    for member in ContractClassification:
        assert DomainClassification(member.value).value == member.value
    for member in DomainClassification:
        assert ContractClassification(member.value).value == member.value
