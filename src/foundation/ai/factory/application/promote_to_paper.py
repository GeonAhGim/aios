"""PromoteToPaper command -- AI-13.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.3 AI-13
`application/promote_to_paper.py` ("confirmation token (AI-4) + risk gate
pass -> create PAPER execution only then (call existing use case)"), §4
**A-1** ("the only path from a proposal to PAPER is `promote_to_paper`, and
if there is no risk-gate call inside it, the test fails (I-10 wiring
proof)"), depends on AI-4 (`gateway/domain/confirm.py::verify_and_consume`,
the pure single-use/TTL/digest judgment) and AI-12
(`application/evaluate_proposal.py`'s `ProposalEvaluation`, this leaf's only
gate on "did the proposal already pass the deterministic validation
pipeline").

Two existing use cases are called, never re-implemented (I-10's own
condition -- a safety/policy component earns "wired", not "re-derived"):
`evaluate_risk_gate` (`risk_gate/application/evaluate_risk_gate.py`,
`GateKind.DEPLOYMENT`, the same kind `paper_control/application/
start_deployment.py::_start_or_resume` evaluates before a deployment may
run) and `request_deployment` (`paper_control/application/
request_deployment.py`, the existing "create a PAPER deployment" command).
`RiskGateDeniedError` is imported from `start_deployment.py` rather than
redefined here -- that module's own docstring already records the lesson
(two independently-defined-but-identically-named exception classes made a
`pytest.raises` pass silently on the wrong one) this leaf does not want to
repeat.

`package_ref` for `request_deployment` is `str(proposal.proposal_id)` --
`request_deployment`'s own docstring states `package_ref` is an opaque
string because FND-04 (`strategy_packages`) has no PAPER_ELIGIBLE package
lifecycle yet; an AI-generated proposal has no package of its own either, so
its `proposal_id` is the only stable opaque reference this leaf can supply
without inventing one.

`ConfirmTicketRepository` is a `Protocol` this module defines and depends
on -- no concrete (Postgres) adapter ships in this leaf, the same choice
`generate_proposal.py`'s `ProposalRepository` Protocol already made (see
that module's docstring) for the same reason: no leaf before this one built
one (`gateway`'s AI-4 row ships `agent_token` storage only, no
`confirm_ticket` table/adapter -- confirmed empty by grep before writing
this module). A concrete adapter is deferred to AI-16 ("tools_propose/paper
+ confirmation token round trip"), the leaf that actually issues a
`ConfirmTicket` at proposal-preview time and is positioned to also own its
Postgres-backed single-use consumption.

Two-step ticket consumption mirrors `domain/confirm.py::verify_and_consume`'s
own docstring split: `verify_and_consume` is the *pure* judgment on the
ticket state the caller already fetched (rich, specific exceptions --
expired/reused/digest-mismatch); `ConfirmTicketRepository.mark_consumed` is
the *atomic* standard-105 conditional UPDATE (`WHERE consumed_at IS NULL`)
that is the actual single-use guarantee under concurrency -- a `None`
return means another request already consumed the ticket in the race
between this function's `get` and `mark_consumed` calls, which this module
folds into the same `ConfirmTicketReusedError` the pure check would have
raised had it observed that row.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable
from uuid import UUID

from src.foundation.ai.factory.contracts.v1 import ProposalEvaluation, StrategyProposal
from src.foundation.ai.gateway.domain.confirm import (
    ConfirmDigestMismatchError,
    ConfirmTicket,
    ConfirmTicketExpiredError,
    ConfirmTicketReusedError,
    verify_and_consume,
)
from src.foundation.connections.ports.repository import ConnectionRepository
from src.foundation.mandates.ports.repository import MandateRepository
from src.foundation.paper_control.application.request_deployment import request_deployment
from src.foundation.paper_control.application.start_deployment import RiskGateDeniedError
from src.foundation.paper_control.contracts.v1 import PaperDeploymentView
from src.foundation.paper_control.ports.repository import PaperControlRepository
from src.foundation.risk_gate.api import GateKind
from src.foundation.risk_gate.application.evaluate_risk_gate import evaluate_risk_gate
from src.foundation.risk_gate.contracts.v1 import RiskOutcome
from src.foundation.risk_gate.ports.repository import RiskGateRepository

__all__ = [
    "ConfirmTicketRepository",
    "PromoteToPaperError",
    "ProposalNotAcceptedError",
    "ProposalEvaluationMismatchError",
    "ConfirmTicketNotFoundError",
    "ConfirmTicketReusedError",
    "ConfirmTicketExpiredError",
    "ConfirmDigestMismatchError",
    "RiskGateDeniedError",
    "promote_to_paper",
]


@runtime_checkable
class ConfirmTicketRepository(Protocol):
    """Storage port `promote_to_paper` depends on -- see module docstring
    for why no concrete adapter ships in this leaf."""

    async def get(self, ticket_id: UUID) -> ConfirmTicket | None: ...

    async def mark_consumed(
        self, ticket_id: UUID, *, execute_digest: str, now: datetime
    ) -> ConfirmTicket | None:
        """Standard-105 conditional UPDATE: `WHERE consumed_at IS NULL`.
        Returns the newly-consumed row, or `None` if the update matched
        zero rows (already consumed by a concurrent request)."""
        ...


class PromoteToPaperError(Exception):
    """Common base for this module's own (non-`evaluate_risk_gate`,
    non-`request_deployment`) rejections."""


class ProposalNotAcceptedError(PromoteToPaperError):
    """AI-12 gate: an evaluation that never passed the validation pipeline
    (`ProposalEvaluation.accepted is False`) must never reach PAPER --
    fail-closed, the same discipline `NoCheckResultsError` applies one
    stage earlier in `evaluate_proposal.py`."""

    def __init__(self, proposal_id: UUID) -> None:
        self.proposal_id = proposal_id
        super().__init__(
            f"promote_to_paper: proposal {proposal_id} has not passed evaluation (accepted=False)"
        )


class ProposalEvaluationMismatchError(PromoteToPaperError):
    """`evaluation.proposal_id` must name the `proposal` this call is
    promoting -- a caller that mixes up an unrelated (even if `accepted`)
    evaluation with this proposal must not silently promote the wrong
    proposal to PAPER."""

    def __init__(self, *, proposal_id: UUID, evaluation_proposal_id: UUID) -> None:
        self.proposal_id = proposal_id
        self.evaluation_proposal_id = evaluation_proposal_id
        super().__init__(
            f"promote_to_paper: evaluation.proposal_id={evaluation_proposal_id} does not match "
            f"proposal.proposal_id={proposal_id}"
        )


class ConfirmTicketNotFoundError(PromoteToPaperError):
    """No ticket exists for `ticket_id` -- fail-closed the same way a
    revoked/unknown agent token does (`gateway/application/authorize.py`):
    "cannot find it" and "it was never valid" are indistinguishable to a
    caller, and both must refuse, not implicitly allow."""

    def __init__(self, ticket_id: UUID) -> None:
        self.ticket_id = ticket_id
        super().__init__(f"promote_to_paper: no confirm ticket for ticket_id={ticket_id}")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def promote_to_paper(
    *,
    proposal: StrategyProposal,
    evaluation: ProposalEvaluation,
    ticket_id: UUID,
    execute_digest: str,
    confirm_repo: ConfirmTicketRepository,
    paper_repo: PaperControlRepository,
    risk_repo: RiskGateRepository,
    mandate_repo: MandateRepository,
    connection_repo: ConnectionRepository,
    tenant_id: UUID,
    actor_subject_id: UUID,
    connection_id: UUID | None,
    adapter_type: str,
    provider_sandbox_account_ref: str,
    endpoint_classification: str,
    idempotency_key: str,
    clock: Callable[[], datetime] = _utcnow,
) -> PaperDeploymentView:
    """§2.3 AI-13 row verbatim: confirm ticket verified+consumed -> risk
    gate must ALLOW -> only then is a PAPER deployment created. Each stage
    raises before the next one runs (no partial promotion on any
    rejection): an unaccepted/mismatched evaluation never reaches the
    ticket check, a rejected/reused/expired ticket never reaches the risk
    gate, and a non-ALLOW risk outcome never reaches `request_deployment`
    (this ordering is what makes the A-1 wiring test meaningful -- deleting
    the `evaluate_risk_gate` call below is the one edit that would let a
    DENYing kill switch still produce a PAPER deployment)."""
    if evaluation.proposal_id != proposal.proposal_id:
        raise ProposalEvaluationMismatchError(
            proposal_id=proposal.proposal_id, evaluation_proposal_id=evaluation.proposal_id
        )
    if not evaluation.accepted:
        raise ProposalNotAcceptedError(proposal.proposal_id)

    now = clock()
    ticket = await confirm_repo.get(ticket_id)
    if ticket is None:
        raise ConfirmTicketNotFoundError(ticket_id)
    verify_and_consume(ticket, execute_digest, now)

    consumed = await confirm_repo.mark_consumed(ticket_id, execute_digest=execute_digest, now=now)
    if consumed is None:
        raise ConfirmTicketReusedError(
            f"ticket {ticket_id}: conditional consume matched no rows "
            "(consumed by a concurrent request between get() and mark_consumed())"
        )

    risk_result = await evaluate_risk_gate(
        risk_repo,
        mandate_repo,
        connection_repo,
        tenant_id=tenant_id,
        gate_kind=GateKind.DEPLOYMENT,
        connection_id=connection_id,
    )
    if risk_result.outcome != RiskOutcome.ALLOW:
        raise RiskGateDeniedError(risk_result.reason_codes)

    return await request_deployment(
        paper_repo,
        mandate_repo,
        tenant_id=tenant_id,
        actor_subject_id=actor_subject_id,
        package_ref=str(proposal.proposal_id),
        connection_id=connection_id,
        adapter_type=adapter_type,
        provider_sandbox_account_ref=provider_sandbox_account_ref,
        endpoint_classification=endpoint_classification,
        idempotency_key=idempotency_key,
    )
