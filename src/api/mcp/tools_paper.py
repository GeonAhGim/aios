"""AI-16 -- `paper`-scope MCP tools (confirmation-ticket round trip over
AI-13's `promote_to_paper`).

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §1 "confirmation
token" (a tool requiring confirmation, e.g. a PAPER execution request,
links its preview and its execution through a server-side one-time
token), §2.3 AI-13 (`application/promote_to_paper.py`), §9 AI-16 DoD
("confirmation token round trip + adversarial: ticket reuse -> 409"),
INVARIANTS.md I-11.

Two tools, matching the spec's own "preview then execute" split:

- `preview_promotion`: computes `action_digest` (a canonical hash of the
  exact promotion parameters, `src.core.risk.hashing.canonical_json`/
  `sha256_hex` -- the same primitives `domain/prompt_registry.py::prompt_hash`
  already uses for the same "one logical value always serializes to the
  same bytes" reason, R-01) and issues a fresh `ConfirmTicket`
  (`PostgresConfirmTicketRepository.issue`, AI-16's own adapter).
- `confirm_promotion`: recomputes the same digest from its own request body
  and calls `promote_to_paper` with `execute_digest`. `verify_and_consume`
  (AI-2, called inside `promote_to_paper`) rejects a mismatched digest, an
  expired ticket, or -- the DoD's named adversarial case -- an
  already-consumed ticket (`ConfirmTicketReusedError` -> 409).

`proposal`/`evaluation` are not trusted from the request body -- both are
re-fetched server-side:

- `proposal`: `PostgresProposalRepository.get_for_tenant` (AI-16), scoped to
  the calling `AgentToken.tenant_id` via the `agent_token` join (cross-tenant
  reads as 404, not the wrong tenant's proposal).
- `evaluation`: reconstructed from the `Experiment` AI-12's
  `evaluate_proposal` already recorded (`ExperimentRepository.get` via
  `experiments/application/query.py`, itself tenant-scoped), not accepted as
  a client-supplied `ProposalEvaluation` object. Accepting a raw client
  `evaluation.accepted=True` would let any `paper`-scoped caller fabricate a
  passing evaluation and walk straight past
  `promote_to_paper`'s `ProposalNotAcceptedError` gate -- exactly the
  "risk gate bypass attempt" class of adversarial input spec §8 names.
  `_reconstruct_evaluation` re-derives `accepted`/`hard_fail_reasons` from
  the same per-check `hard_fail_reasons` `evaluate_proposal.py`'s own
  `_merge_check_metrics` already wrote into `Experiment.metrics` -- reading
  back a stored fact, not re-deciding it (the same I-07 biconditional
  `ProposalEvaluation._check_consistency` already enforces at construction).

  Known limitation (out of this leaf's scope, upstream in AI-10/AI-12): no
  leaf before this one persists an `Experiment` -> `proposal_id` link, so
  this tool cannot cryptographically verify that `experiment_id` actually
  evaluated `proposal_id` -- it trusts the caller to supply a correctly
  paired `(proposal_id, experiment_id)`, the same trust `promote_to_paper`'s
  own signature already places in whatever caller assembles its
  `evaluation` argument (AI-13's own docstring: the caller supplies
  `proposal`/`evaluation` directly).

`actor_subject_id=token.tenant_id` -- `request_deployment`'s
`actor_subject_id` column is `NOT NULL REFERENCES users(user_id)`, and no
leaf up to AI-4 records which human issued a given `AgentToken` (confirmed:
`agent_token` has no `issued_by` column, `issue_token.py`'s own docstring
defers that to "AI-17, not yet implemented"). `tenant_id` is safe to use
here because `tests/integration/conftest.py::create_test_tenant`'s own
convention (and the tenant-bootstrap migration `f4a6b8c0d2e4`) makes a
tenant's PERSONAL `tenant.id` equal to its owning `users.user_id` --
identical to how every other `paper_control` integration test already
passes `actor_subject_id=tenant_id` for tenant-initiated commands.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from src.core.risk.hashing import canonical_json, sha256_hex
from src.foundation.ai.factory.adapters.postgres_proposal_repository import (
    PostgresProposalRepository,
)
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
from src.foundation.ai.factory.contracts.v1 import ProposalEvaluation
from src.foundation.ai.gateway.adapters.postgres_confirm_ticket_repository import (
    PostgresConfirmTicketRepository,
)
from src.foundation.ai.gateway.domain.token_rules import AgentToken, Scope
from src.foundation.connections.adapters.postgres_repository import PostgresConnectionRepository
from src.foundation.experiments.adapters.postgres_repository import PostgresExperimentRepository
from src.foundation.experiments.application.query import ExperimentNotFoundError, get_experiment
from src.foundation.experiments.contracts.v1 import Experiment
from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.paper_control.adapters.postgres_repository import (
    PostgresPaperControlRepository,
)
from src.foundation.paper_control.application.request_deployment import (
    IdempotencyKeyConflictError,
    NoActiveMandateError,
)
from src.foundation.paper_control.contracts.v1 import PaperDeploymentView
from src.foundation.paper_control.domain.rules import InvalidProvenanceError
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.api.mcp.scope_types import ScopeDependency

__all__ = [
    "ConfirmPromotionRequest",
    "PreviewPromotionResponse",
    "PromotionParams",
    "build_router",
]

_CONFIRM_TICKET_TTL = timedelta(minutes=5)


class PromotionParams(BaseModel):
    proposal_id: UUID
    experiment_id: UUID
    connection_id: UUID | None = None
    adapter_type: str
    provider_sandbox_account_ref: str
    endpoint_classification: str
    idempotency_key: str


class ConfirmPromotionRequest(PromotionParams):
    ticket_id: UUID


class PreviewPromotionResponse(BaseModel):
    ticket_id: UUID
    action_digest: str
    expires_at: datetime


def _action_digest(params: PromotionParams) -> str:
    payload: dict[str, Any] = {
        "proposal_id": str(params.proposal_id),
        "experiment_id": str(params.experiment_id),
        "connection_id": str(params.connection_id) if params.connection_id is not None else None,
        "adapter_type": params.adapter_type,
        "provider_sandbox_account_ref": params.provider_sandbox_account_ref,
        "endpoint_classification": params.endpoint_classification,
        "idempotency_key": params.idempotency_key,
    }
    return sha256_hex(canonical_json(payload))


def _reconstruct_evaluation(*, proposal_id: UUID, experiment: Experiment) -> ProposalEvaluation:
    entries = [entry for entry in experiment.metrics.values() if isinstance(entry, dict)]
    hard_fail_reasons = tuple(
        sorted({reason for entry in entries for reason in entry.get("hard_fail_reasons", [])})
    )
    obligations = tuple(sorted({o for entry in entries for o in entry.get("obligations", [])}))
    return ProposalEvaluation(
        proposal_id=proposal_id,
        experiment_id=experiment.experiment_id,
        accepted=not hard_fail_reasons,
        hard_fail_reasons=hard_fail_reasons,
        obligations=obligations,
        metrics=experiment.metrics,
    )


def build_router(require_scope: Callable[[Scope], ScopeDependency]) -> APIRouter:
    router = APIRouter(prefix="/mcp/tools", tags=["mcp-paper"])
    scope_dependency = require_scope(Scope.PAPER)

    @router.post("/preview_promotion", response_model=PreviewPromotionResponse)
    async def preview_promotion_tool(
        body: PromotionParams,
        request: Request,
        token: AgentToken = Depends(scope_dependency),
    ) -> PreviewPromotionResponse:
        pool: asyncpg.Pool = request.app.state.pool
        confirm_repo = PostgresConfirmTicketRepository(pool)
        now = datetime.now(timezone.utc)
        ticket = await confirm_repo.issue(
            tenant_id=token.tenant_id,
            created_by_token=token.token_id,
            action_digest=_action_digest(body),
            expires_at=now + _CONFIRM_TICKET_TTL,
        )
        return PreviewPromotionResponse(
            ticket_id=ticket.ticket_id,
            action_digest=ticket.action_digest,
            expires_at=ticket.expires_at,
        )

    @router.post("/confirm_promotion", response_model=PaperDeploymentView)
    async def confirm_promotion_tool(
        body: ConfirmPromotionRequest,
        request: Request,
        token: AgentToken = Depends(scope_dependency),
    ) -> PaperDeploymentView:
        pool: asyncpg.Pool = request.app.state.pool
        proposal_repo = PostgresProposalRepository(pool)
        experiment_repo = PostgresExperimentRepository(pool)

        proposal = await proposal_repo.get_for_tenant(body.proposal_id, tenant_id=token.tenant_id)
        if proposal is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"proposal {body.proposal_id} not found")
        try:
            experiment = await get_experiment(experiment_repo, token.tenant_id, body.experiment_id)
        except ExperimentNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        evaluation = _reconstruct_evaluation(
            proposal_id=proposal.proposal_id, experiment=experiment
        )

        try:
            return await promote_to_paper(
                proposal=proposal,
                evaluation=evaluation,
                ticket_id=body.ticket_id,
                execute_digest=_action_digest(body),
                confirm_repo=PostgresConfirmTicketRepository(pool),
                paper_repo=PostgresPaperControlRepository(pool),
                risk_repo=PostgresRiskGateRepository(pool),
                mandate_repo=PostgresMandateRepository(pool),
                connection_repo=PostgresConnectionRepository(pool),
                tenant_id=token.tenant_id,
                actor_subject_id=token.tenant_id,
                connection_id=body.connection_id,
                adapter_type=body.adapter_type,
                provider_sandbox_account_ref=body.provider_sandbox_account_ref,
                endpoint_classification=body.endpoint_classification,
                idempotency_key=body.idempotency_key,
            )
        except ProposalNotAcceptedError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except ProposalEvaluationMismatchError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except ConfirmTicketNotFoundError as exc:
            raise HTTPException(
                status.HTTP_428_PRECONDITION_REQUIRED, f"AI_CONFIRM_REQUIRED: {exc}"
            ) from exc
        except ConfirmTicketExpiredError as exc:
            raise HTTPException(
                status.HTTP_428_PRECONDITION_REQUIRED, f"AI_CONFIRM_REQUIRED: {exc}"
            ) from exc
        except ConfirmTicketReusedError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, f"confirm ticket reuse: {exc}") from exc
        except ConfirmDigestMismatchError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, f"AI_CONFIRM_MISMATCH: {exc}") from exc
        except RiskGateDeniedError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"risk gate denied: {exc}") from exc
        except NoActiveMandateError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except InvalidProvenanceError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        except IdempotencyKeyConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    return router
