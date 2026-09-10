"""CM-5 통합테스트 — 실 DB(`TEST_DATABASE_URL`) 대상.

Spec: docs/specs/L4_compliance_and_regulatory_v1.0.md#§9 CM-5, CM-A3
("번들 활성화는 작성자와 다른 승인자를 요구한다").

DoD(task-2118) (1): U1이 propose_amendment로 개정안을 만든 뒤 같은 U1이
activate_revision을 호출하면 400(작성자==승인자)으로 거부되고, U2가
호출하면 활성화에 성공한다 — 두 테스트는 activate 호출의 `subject_id` 하나만
다르다(그 외 입력 전부 동일), `_propose_pending_amendment`로 공유한다.
DoD (5): 승인 레코드(`foundation_audit_event`)에 proposer_id/approver_id가
서로 다른 값으로 저장됨을 실DB로 단언한다.
"""

from __future__ import annotations

import time
from uuid import UUID, uuid4

import pytest

from src.foundation.mandates.application.activate_revision import (
    InvalidRevisionStateError,
    SelfApprovalNotAllowedError,
)
from src.foundation.mandates.application.activate_revision import (
    activate_revision as activate_revision_command,
)
from src.foundation.mandates.application.create_draft_mandate import create_draft_mandate
from src.foundation.mandates.application.propose_amendment import propose_amendment
from src.foundation.mandates.contracts.v1 import MandateRevisionState, MandateRevisionView
from tests.foundation.integration.mandates.conftest import default_rules
from tests.integration.conftest import create_test_tenant


async def _activated_tenant(pool, repo, trust_repo) -> UUID:
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


async def _propose_pending_amendment(
    pool, repo, trust_repo, audit_repo, *, proposer_id: UUID
) -> tuple[UUID, MandateRevisionView]:
    """작성자가 `proposer_id`인, 활성화 대기 중인(비-material) PROPOSED
    revision을 하나 만든다. `max_total_exposure_pct`를 baseline(80.0)보다
    낮춰(50.0) material change 게이트(재인증)를 건드리지 않는다 — 이 테스트
    스위트는 CM-5(작성자≠승인자)만 겨눈다."""
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    proposed = await propose_amendment(
        repo,
        tenant_id=tenant_id,
        rules=default_rules(max_total_exposure_pct=50.0),
        proposer_id=proposer_id,
        audit_repo=audit_repo,
    )
    assert proposed.cooling_off_started_at is None  # non-material — 회귀 대조
    return tenant_id, proposed


async def test_activation_by_the_proposer_themself_is_rejected(pool, repo, trust_repo, audit_repo):
    u1 = uuid4()
    tenant_id, proposed = await _propose_pending_amendment(
        pool, repo, trust_repo, audit_repo, proposer_id=u1
    )

    with pytest.raises(SelfApprovalNotAllowedError):
        await activate_revision_command(
            repo,
            trust_repo,
            tenant_id=tenant_id,
            subject_id=u1,
            revision_id=proposed.id,
            reauthenticated=False,
            audit_repo=audit_repo,
        )

    unchanged = await repo.get_revision(proposed.id)
    assert unchanged.state == MandateRevisionState.PROPOSED


async def test_activation_by_a_different_subject_succeeds(pool, repo, trust_repo, audit_repo):
    """위와 입력이 `subject_id`(u1 -> u2) 하나만 다르다."""
    u1 = uuid4()
    u2 = uuid4()
    tenant_id, proposed = await _propose_pending_amendment(
        pool, repo, trust_repo, audit_repo, proposer_id=u1
    )

    activated = await activate_revision_command(
        repo,
        trust_repo,
        tenant_id=tenant_id,
        subject_id=u2,
        revision_id=proposed.id,
        reauthenticated=False,
        audit_repo=audit_repo,
    )

    assert activated.state == MandateRevisionState.ACTIVE
    assert activated.max_total_exposure_pct == 50.0


async def test_activation_audit_row_stores_distinct_proposer_and_approver(
    pool, repo, trust_repo, audit_repo
):
    u1 = uuid4()
    u2 = uuid4()
    _tenant_id, proposed = await _propose_pending_amendment(
        pool, repo, trust_repo, audit_repo, proposer_id=u1
    )

    await activate_revision_command(
        repo,
        trust_repo,
        tenant_id=_tenant_id,
        subject_id=u2,
        revision_id=proposed.id,
        reauthenticated=False,
        audit_repo=audit_repo,
    )

    activated_event = await audit_repo.get_latest_event(
        "mandate_revision", proposed.id, action="mandate_revision_activated"
    )
    assert activated_event is not None
    assert activated_event.actor_subject_id == u2
    assert activated_event.payload["proposer_id"] == str(u1)
    assert activated_event.payload["approver_id"] == str(u2)
    assert activated_event.payload["proposer_id"] != activated_event.payload["approver_id"]


async def test_replaying_activation_after_it_already_succeeded_is_rejected(
    pool, repo, trust_repo, audit_repo
):
    """리플레이 증명(DEEPEN task-2862): revision A가 이미 ACTIVE로 전이된 뒤,
    같은 activate 커맨드를(같은 revision_id로) 다시 보내면 상태 가드
    (`state in (DRAFT, PROPOSED)`)가 이를 거부해야 한다 — 네트워크 재시도나
    메시지 중복 전달로 같은 커맨드가 두 번 도착해도 두 번째 활성화가 조용히
    통과하거나 감사 로그가 중복 기록되지 않는다는 증거. 첫 성공의 승인자(u2)가
    재전송해도, 다른 승인자(u3)가 재전송해도 둘 다 거부됨을 확인한다."""
    u1 = uuid4()
    u2 = uuid4()
    u3 = uuid4()
    tenant_id, proposed = await _propose_pending_amendment(
        pool, repo, trust_repo, audit_repo, proposer_id=u1
    )

    activated = await activate_revision_command(
        repo,
        trust_repo,
        tenant_id=tenant_id,
        subject_id=u2,
        revision_id=proposed.id,
        reauthenticated=False,
        audit_repo=audit_repo,
    )
    assert activated.state == MandateRevisionState.ACTIVE

    for replayer in (u2, u3):
        with pytest.raises(InvalidRevisionStateError):
            await activate_revision_command(
                repo,
                trust_repo,
                tenant_id=tenant_id,
                subject_id=replayer,
                revision_id=proposed.id,
                reauthenticated=False,
                audit_repo=audit_repo,
            )

    activated_events = await _all_activation_events(pool, proposed.id)
    assert len(activated_events) == 1  # 재전송이 감사 로그를 중복 기록하지 않았다


async def _all_activation_events(pool, revision_id: UUID) -> list[UUID]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM foundation_audit_event "
            "WHERE aggregate_type = 'mandate_revision' AND aggregate_id = $1 "
            "AND action = 'mandate_revision_activated'",
            revision_id,
        )
    return [row["id"] for row in rows]


async def test_propose_and_activate_cycle_meets_throughput_budget(
    pool, repo, trust_repo, audit_repo
):
    """수치 성능 단언(DEEPEN task-2862): propose_amendment + activate_revision
    한 사이클(각각 여러 실DB 왕복 포함: 조회 3~4회 + INSERT/UPDATE + 감사
    INSERT)을 번갈아 다른 제안자/승인자로 20회 반복한 처리량이 하한
    아래로 떨어지면 회귀로 잡는다. 이전에는 성공/실패 여부만 확인했을 뿐
    수치 상한이 전혀 없었다."""
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    u1, u2 = uuid4(), uuid4()

    n_cycles = 20
    started = time.perf_counter()
    for i in range(n_cycles):
        proposer, approver = (u1, u2) if i % 2 == 0 else (u2, u1)
        proposed = await propose_amendment(
            repo,
            tenant_id=tenant_id,
            rules=default_rules(max_total_exposure_pct=79.0 - i),
            proposer_id=proposer,
            audit_repo=audit_repo,
        )
        activated = await activate_revision_command(
            repo,
            trust_repo,
            tenant_id=tenant_id,
            subject_id=approver,
            revision_id=proposed.id,
            reauthenticated=False,
            audit_repo=audit_repo,
        )
        assert activated.state == MandateRevisionState.ACTIVE
    elapsed_s = time.perf_counter() - started
    throughput = n_cycles / elapsed_s

    assert elapsed_s < 15.0, (
        f"{n_cycles}회 propose+activate 사이클이 {elapsed_s:.3f}s 걸림 (예산 15.0s)"
    )
    assert throughput > 2.0, f"처리량 {throughput:.2f} cycles/s < 2.0 cycles/s 하한"
