"""CM-5 적대적 스위트 — 실 DB(`TEST_DATABASE_URL`) 대상.

Spec: docs/specs/L4_compliance_and_regulatory_v1.0.md#§9 CM-5, CM-A3.

DoD(task-2118) (2) 우회 차단: 작성자 본인이 다른 사용자 이름의 개정안을 하나
더 끼워 넣어도(디코이), 자기가 만든 revision을 자기가 활성화하는 경로는
여전히 거부된다 — proposer 조회가 `revision_id`(aggregate_id) 단위로 정확히
스코프된다는 증거.

DoD (3) 배선 증명: 판정 분기는 `segregation_of_duty.
assert_actor_not_counterparty` 호출 한 곳으로만 존재해야 한다 — 이 테스트는
그 호출을 (활성화 모듈 네임스페이스에서) no-op으로 치환하고, 그러면 (1)의
거부 시나리오가 실제로 통과(activate 성공)로 바뀌는 것을 실행으로 증명한다
(주석이 아니라 실행).
"""
from __future__ import annotations

from uuid import uuid4

import pytest

import src.foundation.mandates.application.activate_revision as activate_revision_module
from src.foundation.mandates.application.activate_revision import (
    SelfApprovalNotAllowedError,
)
from src.foundation.mandates.application.activate_revision import (
    activate_revision as activate_revision_command,
)
from src.foundation.mandates.application.create_draft_mandate import create_draft_mandate
from src.foundation.mandates.application.propose_amendment import propose_amendment
from src.foundation.mandates.contracts.v1 import MandateRevisionState
from tests.foundation.integration.mandates.conftest import default_rules
from tests.integration.conftest import create_test_tenant


async def _activated_tenant(pool, repo, trust_repo):
    tenant_id = await create_test_tenant(pool)
    draft = await create_draft_mandate(
        repo, tenant_id=tenant_id, subject_id=tenant_id, rules=default_rules()
    )
    await activate_revision_command(
        repo,
        trust_repo,
        tenant_id=tenant_id,
        subject_id=tenant_id,
        revision_id=draft.id,
        reauthenticated=False,
    )
    return tenant_id


async def test_decoy_proposal_under_another_name_does_not_unblock_self_activation(
    pool, repo, trust_repo, audit_repo
):
    """U1이 revision A를 제안하고, 곧이어 U2 이름으로 무관한 revision B를
    끼워 넣어도(디코이), A를 U1 스스로 활성화하는 시도는 여전히 거부된다."""
    u1 = uuid4()
    u2 = uuid4()
    tenant_id = await _activated_tenant(pool, repo, trust_repo)

    revision_a = await propose_amendment(
        repo,
        tenant_id=tenant_id,
        rules=default_rules(max_total_exposure_pct=50.0),
        proposer_id=u1,
        audit_repo=audit_repo,
    )
    revision_b = await propose_amendment(
        repo,
        tenant_id=tenant_id,
        rules=default_rules(max_single_instrument_pct=5.0),
        proposer_id=u2,
        audit_repo=audit_repo,
    )
    assert revision_b.id != revision_a.id  # 디코이가 실제로 별도 revision임을 확인

    with pytest.raises(SelfApprovalNotAllowedError):
        await activate_revision_command(
            repo,
            trust_repo,
            tenant_id=tenant_id,
            subject_id=u1,
            revision_id=revision_a.id,
            reauthenticated=False,
            audit_repo=audit_repo,
        )

    unchanged = await repo.get_revision(revision_a.id)
    assert unchanged.state == MandateRevisionState.PROPOSED


async def test_removing_segregation_of_duty_call_lets_self_approval_through(
    pool, repo, trust_repo, audit_repo, monkeypatch
):
    """배선 증명 — `assert_actor_not_counterparty` 호출을 no-op으로 치환하면
    (동일 시나리오가) 더 이상 거부되지 않는다. 즉 activate_revision.py의
    자기승인 차단은 오직 이 한 번의 호출에서 나온다(우회 경로·중복 인라인
    판정이 없다)."""
    monkeypatch.setattr(
        activate_revision_module,
        "assert_actor_not_counterparty",
        lambda *args, **kwargs: None,
    )

    u1 = uuid4()
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    revision_a = await propose_amendment(
        repo,
        tenant_id=tenant_id,
        rules=default_rules(max_total_exposure_pct=50.0),
        proposer_id=u1,
        audit_repo=audit_repo,
    )

    activated = await activate_revision_command(
        repo,
        trust_repo,
        tenant_id=tenant_id,
        subject_id=u1,
        revision_id=revision_a.id,
        reauthenticated=False,
        audit_repo=audit_repo,
    )

    assert activated.state == MandateRevisionState.ACTIVE
