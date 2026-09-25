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

import asyncio
from uuid import uuid4

import asyncpg
import pytest

import src.foundation.mandates.application.activate_revision as activate_revision_module
from src.foundation.mandates.adapters.postgres_repository import (
    ConcurrencyConflictError,
    PostgresMandateRepository,
)
from src.foundation.mandates.application.activate_revision import (
    SelfApprovalNotAllowedError,
)
from src.foundation.mandates.application.activate_revision import (
    activate_revision as activate_revision_command,
)
from src.foundation.mandates.application.create_draft_mandate import create_draft_mandate
from src.foundation.mandates.application.propose_amendment import propose_amendment
from src.foundation.mandates.contracts.v1 import MandateRevisionState
from tests.foundation.integration.mandates.conftest import _asyncpg_dsn, default_rules
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


async def test_proposer_lookup_failure_fails_closed_does_not_activate(
    pool, repo, trust_repo, audit_repo, monkeypatch
):
    """실패 주입(DEEPEN task-2862): `audit_repo.get_latest_event`(proposer
    조회, DB 왕복)이 커넥션 드롭 등으로 예외를 던지면, `activate_revision`은
    그 예외를 삼키지 않고 그대로 전파해야 한다 — 만약 여기서 예외를 삼키고
    "proposer 없음"으로 취급해 통과시킨다면, 진짜 proposer가 있는데도 조회
    실패를 가장해 자기승인 차단(CM-A3)을 우회할 수 있는 구멍이 생긴다. 이
    테스트는 정상적으로는 서로 다른 제안자/승인자라 성공했을 시나리오에서
    조회만 실패하게 만들어, 실패가 fail-closed(활성화 자체가 일어나지 않음)로
    이어짐을 실증한다."""

    async def _raise_connection_drop(*args: object, **kwargs: object) -> None:
        raise ConnectionError("simulated DB connection drop during proposer lookup")

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

    monkeypatch.setattr(audit_repo, "get_latest_event", _raise_connection_drop)

    with pytest.raises(ConnectionError):
        await activate_revision_command(
            repo,
            trust_repo,
            tenant_id=tenant_id,
            subject_id=u2,
            revision_id=revision_a.id,
            reauthenticated=False,
            audit_repo=audit_repo,
        )

    unchanged = await repo.get_revision(revision_a.id)
    assert unchanged.state == MandateRevisionState.PROPOSED  # 실패했으니 활성화되지 않았다


async def test_concurrent_activation_from_different_instances_only_one_wins(
    pool, repo, trust_repo, audit_repo
):
    """D3 다중 인스턴스 증명(DEEPEN task-2862): 서로 다른 실제 asyncpg
    커넥션 풀(별도 앱 "인스턴스"를 흉내)을 쓰는 두 인스턴스가, 같이 관찰한
    `expected_active_revision_id`(현재 ACTIVE revision)로 서로 다른 PROPOSED
    revision을 정확히 동시에 `activate_revision`(리포지토리 CAS — 모듈
    docstring이 "진짜 직렬화 지점"이라 부르는 지점)으로 activate하려
    시도한다. 실제 DB 레벨에서 정확히 하나만 이기고 나머지는
    `ConcurrencyConflictError`로 거부되어야 한다 — 순차 await였다면(기존
    디코이 테스트처럼) 이 경합이 전혀 재현되지 않는다.

    상위 커맨드(`activate_revision_command`)를 통째로 동시에 두 번 호출하는
    방식은 이 환경에서 쓰지 않는다: 그 커맨드는 CAS 직전에
    `get_active_revision`을 material-change 판단용으로 다시 읽는데, 두 커맨드
    실행이 이 환경(Windows ProactorEventLoop)에서 사실상 순차로 끝나는 것이
    관측됐다 — 패자가 CAS에 도달하기도 전에 이미 승자로 바뀐
    current_active와 자기 규칙을 비교해 CAS 경합과 무관한
    `MaterialChangeRequiresReauthError`로 거부되어, 이 테스트가 증명하려는
    CAS 경합 자체가 재현되지 않는 거짓 통과를 만들었다(수정 전 실측:
    5/5 결정론적으로 이 오분류 발생). 그래서 두 인스턴스가 **미리 고정해
    공유하는** `expected_active_revision_id`로 리포지토리의
    `activate_revision`(CAS)만 직접 동시 호출해, 상위 커맨드의 비즈니스
    규칙과 무관하게 Postgres 행 잠금 레벨의 경합만 결정론적으로 재현한다."""
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

    mandate = await repo.get_mandate(tenant_id)
    current_active = await repo.get_active_revision(mandate.id)

    n_instances = 2
    concurrent_pool = await asyncpg.create_pool(
        _asyncpg_dsn(), min_size=n_instances, max_size=n_instances
    )
    try:
        repo_1 = PostgresMandateRepository(concurrent_pool)
        repo_2 = PostgresMandateRepository(concurrent_pool)

        results = await asyncio.gather(
            repo_1.activate_revision(
                mandate.id, revision_a.id, expected_active_revision_id=current_active.id
            ),
            repo_2.activate_revision(
                mandate.id, revision_b.id, expected_active_revision_id=current_active.id
            ),
            return_exceptions=True,
        )
    finally:
        await concurrent_pool.close()

    successes = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1, f"정확히 한 인스턴스만 이겨야 한다: {results}"
    assert len(failures) == 1
    assert isinstance(failures[0], ConcurrencyConflictError)

    refreshed_mandate = await repo.get_mandate(tenant_id)
    winner_id = successes[0].id
    assert refreshed_mandate.active_revision_id == winner_id

    loser_id = revision_b.id if winner_id == revision_a.id else revision_a.id
    loser_revision = await repo.get_revision(loser_id)
    assert loser_revision.state == MandateRevisionState.PROPOSED  # 패자는 활성화되지 않았다
