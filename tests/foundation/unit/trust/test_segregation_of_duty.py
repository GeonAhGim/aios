"""PLT-43 segregation_of_duty.py 단위테스트 — DB 없이 순수 함수만 검증."""
from uuid import uuid4

import pytest

from src.foundation.trust.domain.rules.segregation_of_duty import (
    SegregationOfDutyViolation,
    assert_actor_not_counterparty,
)


def test_allows_distinct_actor_and_counterparty() -> None:
    actor = uuid4()
    counterparty = uuid4()
    assert_actor_not_counterparty(actor, counterparty, action="mandate.activate")


def test_allows_when_counterparty_not_yet_assigned() -> None:
    """DUAL 승인의 첫 서명 전처럼 아직 counterparty가 없으면 통과한다."""
    assert_actor_not_counterparty(uuid4(), None, action="approval_request.dual_second_sign")


def test_rejects_identical_actor_and_counterparty() -> None:
    same_id = uuid4()
    with pytest.raises(SegregationOfDutyViolation) as excinfo:
        assert_actor_not_counterparty(same_id, same_id, action="break_glass.approve_grant")
    assert excinfo.value.actor_id == same_id
    assert excinfo.value.action == "break_glass.approve_grant"
    assert str(same_id) in str(excinfo.value)


def test_rejects_identical_non_uuid_ids() -> None:
    """UUID뿐 아니라 값 동등성을 지원하는 어떤 식별자 타입에도 동작한다."""
    with pytest.raises(SegregationOfDutyViolation):
        assert_actor_not_counterparty("user-1", "user-1", action="mandate.propose_amendment")
