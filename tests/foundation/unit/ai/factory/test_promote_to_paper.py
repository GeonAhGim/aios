"""Unit tests for `src/foundation/ai/factory/application/promote_to_paper.py`
-- task-2648 AI-13 DoD ("게이트 미호출 시 실패(A-1)"). D2 depth
(ADR-2026-09-09-C): negative >=3, failure injection 1, numeric performance
assertion 1, gate-red reproduction 1, plus the leaf's own A-1 adversarial
wiring proof (INVARIANTS.md I-10/I-11). Fakes/builders live in
`_promote_to_paper_fakes.py` (file policy ADR-2026-09-10-C §7).
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest

from src.foundation.ai.factory.application.promote_to_paper import (
    ConfirmDigestMismatchError,
    ConfirmTicketExpiredError,
    ConfirmTicketNotFoundError,
    ConfirmTicketReusedError,
    ProposalEvaluationMismatchError,
    ProposalNotAcceptedError,
    RiskGateDeniedError,
    promote_to_paper,
)
from src.foundation.risk_gate.domain.models import SafetyControl, SafetyControlState, SafetyScope
from tests.foundation.unit.ai.factory._promote_to_paper_fakes import (
    DIGEST,
    NOW,
    Deps,
    FakeRiskGateRepository,
    make_evaluation,
    make_proposal,
    make_ticket,
)


async def _promote(
    deps: Deps,
    *,
    proposal: Any = None,
    evaluation: Any = None,
    ticket: Any = None,
    execute_digest: str = DIGEST,
    idempotency_key: str | None = None,
    clock: Any = lambda: NOW,
):
    proposal = proposal if proposal is not None else make_proposal()
    evaluation = evaluation if evaluation is not None else make_evaluation(proposal)
    ticket = ticket if ticket is not None else make_ticket()
    if not deps.confirm_repo.has(ticket.ticket_id):
        deps.confirm_repo.seed(ticket)
    return await promote_to_paper(
        proposal=proposal,
        evaluation=evaluation,
        ticket_id=ticket.ticket_id,
        execute_digest=execute_digest,
        confirm_repo=deps.confirm_repo,
        paper_repo=deps.paper_repo,
        risk_repo=deps.risk_repo,
        mandate_repo=deps.mandate_repo,
        connection_repo=deps.connection_repo,
        tenant_id=deps.tenant_id,
        actor_subject_id=uuid4(),
        connection_id=None,
        adapter_type="paper_sim",
        provider_sandbox_account_ref="acct-1",
        endpoint_classification="sandbox",
        idempotency_key=idempotency_key if idempotency_key is not None else str(uuid4()),
        clock=clock,
    )


# --- 정상 경로 ---


@pytest.mark.asyncio
async def test_promote_to_paper_creates_paper_deployment_on_confirm_and_allow() -> None:
    deps = Deps()
    proposal = make_proposal()
    view = await _promote(deps, proposal=proposal)

    assert view.package_ref == str(proposal.proposal_id)
    assert deps.paper_repo.insert_deployment_calls == 1
    assert deps.risk_repo.list_active_controls_calls == 1  # A-1: the gate was actually called


# --- 부정 테스트 (>=3) ---


@pytest.mark.asyncio
async def test_promote_to_paper_rejects_unaccepted_evaluation() -> None:
    deps = Deps()
    proposal = make_proposal()
    with pytest.raises(ProposalNotAcceptedError):
        await _promote(
            deps, proposal=proposal, evaluation=make_evaluation(proposal, accepted=False)
        )
    assert deps.paper_repo.insert_deployment_calls == 0
    assert deps.risk_repo.list_active_controls_calls == 0  # never reaches the gate


@pytest.mark.asyncio
async def test_promote_to_paper_rejects_evaluation_for_a_different_proposal() -> None:
    deps = Deps()
    proposal = make_proposal()
    other_evaluation = make_evaluation(make_proposal())  # different proposal_id
    with pytest.raises(ProposalEvaluationMismatchError):
        await _promote(deps, proposal=proposal, evaluation=other_evaluation)
    assert deps.paper_repo.insert_deployment_calls == 0


@pytest.mark.asyncio
async def test_promote_to_paper_rejects_unknown_ticket_id() -> None:
    deps = Deps()
    proposal = make_proposal()
    evaluation = make_evaluation(proposal)
    with pytest.raises(ConfirmTicketNotFoundError):
        await promote_to_paper(
            proposal=proposal,
            evaluation=evaluation,
            ticket_id=uuid4(),  # never seeded into confirm_repo
            execute_digest=DIGEST,
            confirm_repo=deps.confirm_repo,
            paper_repo=deps.paper_repo,
            risk_repo=deps.risk_repo,
            mandate_repo=deps.mandate_repo,
            connection_repo=deps.connection_repo,
            tenant_id=deps.tenant_id,
            actor_subject_id=uuid4(),
            connection_id=None,
            adapter_type="paper_sim",
            provider_sandbox_account_ref="acct-1",
            endpoint_classification="sandbox",
            idempotency_key=str(uuid4()),
            clock=lambda: NOW,
        )
    assert deps.paper_repo.insert_deployment_calls == 0


@pytest.mark.asyncio
async def test_promote_to_paper_rejects_digest_mismatch() -> None:
    """§3 `AI_CONFIRM_MISMATCH` (409) -- preview digest != execution digest."""
    deps = Deps()
    ticket = make_ticket(digest=DIGEST)
    with pytest.raises(ConfirmDigestMismatchError):
        await _promote(deps, ticket=ticket, execute_digest="b" * 64)
    assert deps.paper_repo.insert_deployment_calls == 0


@pytest.mark.asyncio
async def test_promote_to_paper_rejects_expired_ticket() -> None:
    deps = Deps()
    ticket = make_ticket(expires_at=NOW - timedelta(seconds=1))
    with pytest.raises(ConfirmTicketExpiredError):
        await _promote(deps, ticket=ticket)
    assert deps.paper_repo.insert_deployment_calls == 0


@pytest.mark.asyncio
async def test_promote_to_paper_rejects_reused_ticket() -> None:
    """§6 "confirmation ticket reuse" -> 409, audited."""
    deps = Deps()
    proposal = make_proposal()
    ticket = make_ticket()
    deps.confirm_repo.seed(ticket)

    first = await _promote(
        deps, proposal=proposal, evaluation=make_evaluation(proposal), ticket=ticket
    )
    assert first.package_ref == str(proposal.proposal_id)

    second_proposal = make_proposal()
    with pytest.raises(ConfirmTicketReusedError):
        await _promote(
            deps,
            proposal=second_proposal,
            evaluation=make_evaluation(second_proposal),
            ticket=ticket,  # same ticket, already consumed above
        )
    assert deps.paper_repo.insert_deployment_calls == 1  # only the first promotion


# --- A-1 적대적: 리스크 게이트 배선 증명 (I-10/I-11) ---


@pytest.mark.asyncio
async def test_promote_to_paper_blocked_by_active_kill_switch_never_creates_deployment() -> None:
    """A-1: risk_gate가 DENY(예: kill switch ACTIVE)면 PAPER 배포가 생성되지
    않는다 -- 이 리프가 실제로 `evaluate_risk_gate`를 호출해 그 결과에 순종한다는
    배선 증명(I-10)."""
    deps = Deps()
    deps.risk_repo = FakeRiskGateRepository(
        active_controls=(
            SafetyControl(
                id=uuid4(),
                scope=SafetyScope.GLOBAL,
                scope_ref="",
                state=SafetyControlState.ACTIVE,
                reason="red-team kill switch",
                actor_subject_id=uuid4(),
                fence_token=1,
            ),
        )
    )
    with pytest.raises(RiskGateDeniedError) as exc_info:
        await _promote(deps)
    assert "RISK_KILL_SWITCH_ACTIVE_GLOBAL" in exc_info.value.reason_codes
    assert deps.risk_repo.list_active_controls_calls == 1  # the gate really ran
    assert deps.paper_repo.insert_deployment_calls == 0  # and PAPER was never created


# --- 실패 주입 ---


@pytest.mark.asyncio
async def test_promote_to_paper_propagates_deployment_insert_failure() -> None:
    """실패 주입: risk gate까지 통과한 뒤 배포 저장 자체가 예외(DB 장애 등)를
    내면 조용히 삼키지 않고 그대로 전파돼야 한다 -- 성공으로 위장 금지."""
    deps = Deps()
    deps.paper_repo.fail_insert_deployment = ConnectionError("paper_deployment unreachable")
    with pytest.raises(ConnectionError):
        await _promote(deps)
    assert deps.risk_repo.list_active_controls_calls == 1  # the gate still ran first


# --- 수치 성능 단언 ---

_PROMOTE_BUDGET_MS = 100.0
"""§7 SLO에 AI-13 전용 상한은 없다 -- 이 leaf는 공급자 호출 없이 인메모리
confirm/risk/mandate/paper 저장소 왕복만 하므로 AI-9의 500ms 공급자 포함
상한보다 훨씬 좁게 잡는다."""


async def _promote_latencies_ms(iterations: int = 20) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        deps = Deps()
        started = time.perf_counter()
        await _promote(deps)
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


@pytest.mark.asyncio
async def test_promote_to_paper_p95_latency_within_budget() -> None:
    samples = await _promote_latencies_ms()
    p95_ms = _p95(samples)
    print(f"[AI-13 promote_to_paper] p95={p95_ms:.2f}ms budget<{_PROMOTE_BUDGET_MS:.0f}ms")
    assert p95_ms < _PROMOTE_BUDGET_MS


# --- 게이트 적색 재현 ---


@pytest.mark.asyncio
async def test_gate_red_budget_actually_fails_past_budget() -> None:
    samples = await _promote_latencies_ms(iterations=5)
    absurdly_low_budget_ms = 1e-9
    with pytest.raises(AssertionError):
        assert _p95(samples) < absurdly_low_budget_ms
