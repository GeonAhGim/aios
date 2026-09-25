"""AI-17 -- `/v1/ai/{tokens,proposals,experiments}` (human session, `login_required`).

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.1 AI-17
(`api/routers/ai.py`: "token issue/rotate/revoke, proposal list, experiment
query -- human session, login_required"), §9 AI-17 DoD ("cross-tenant 404").

This is the human-facing counterpart to `src/api/mcp/*` -- the MCP tools
(AI-15/16) are the *agent*-facing surface, authenticated by `AgentToken` and
scoped to `read|research|propose|paper`; this router is authenticated by the
ordinary human JWT (`get_current_user`, same as every other `foundation/*`
router) and is how a human manages the agent tokens themselves plus reviews
what agents have produced. `tenant_id=user.user_id` follows the same
convention `foundation/connections.py`/`mandates.py`/`paper_control.py`
already use -- the personal-tenant identity `get_tenant_context`'s own
docstring names (`X-Tenant-Id` header omitted).

Rule 71 §6: this router only wires auth/dependency injection/transport
validation/command invocation -- domain exceptions are not caught here,
`exception_registry_foundation_ai_gateway.py` translates them via
EXCEPTION_MAP (zero raw HTTPException, `tests/unit/api/
test_no_raw_http_exception.py` is not whitelisted for this file).

Token "rotation" is not a leaf of its own anywhere in the spec's module
table -- it is composed here from the two AI-4 use cases that already exist
(`revoke_token` then `issue_token` with the just-revoked token's own
scopes/allow_instruments/notional_cap carried forward), matching AI-17's
"+ integration" file-table annotation rather than adding a new
`application/rotate_token.py` leaf.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, status

from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import get_current_user, get_pool
from src.api.schemas.ai import (
    AgentTokenListResponse,
    AgentTokenView,
    IssueAgentTokenRequest,
    IssuedAgentTokenView,
    ProposalListResponse,
    RevokeAgentTokenRequest,
    RotateAgentTokenRequest,
)
from src.foundation.ai.factory.adapters.postgres_proposal_repository import (
    PostgresProposalRepository,
)
from src.foundation.ai.factory.application.errors import ProposalNotFoundError
from src.foundation.ai.factory.contracts.v1 import StrategyProposal
from src.foundation.ai.gateway.adapters.postgres_token_repository import (
    PostgresAgentTokenRepository,
)
from src.foundation.ai.gateway.application.issue_token import issue_token
from src.foundation.ai.gateway.application.revoke_token import revoke_token
from src.foundation.experiments.adapters.postgres_repository import PostgresExperimentRepository
from src.foundation.experiments.application.query import get_experiment, get_lineage_chain
from src.foundation.experiments.contracts.v1 import Experiment
from src.services.auth_service import User

router = APIRouter(prefix="/v1/ai", tags=["ai-gateway"])


def get_agent_token_repository(
    pool: asyncpg.Pool = Depends(get_pool),
) -> PostgresAgentTokenRepository:
    return PostgresAgentTokenRepository(pool)


def get_proposal_repository(pool: asyncpg.Pool = Depends(get_pool)) -> PostgresProposalRepository:
    return PostgresProposalRepository(pool)


def get_experiment_repository(
    pool: asyncpg.Pool = Depends(get_pool),
) -> PostgresExperimentRepository:
    return PostgresExperimentRepository(pool)


# --- tokens -----------------------------------------------------------------


@router.post("/tokens", status_code=status.HTTP_201_CREATED)
async def issue_agent_token(
    body: IssueAgentTokenRequest,
    user: User = Depends(get_current_user),
    repo: PostgresAgentTokenRepository = Depends(get_agent_token_repository),
) -> ApiResponse[IssuedAgentTokenView]:
    issued = await issue_token(
        repo,
        tenant_id=user.user_id,
        scopes=frozenset(body.scopes),
        allow_instruments=frozenset(body.allow_instruments),
        notional_cap=body.notional_cap,
        ttl=timedelta(seconds=body.ttl_seconds),
        now=datetime.now(timezone.utc),
    )
    return ok(IssuedAgentTokenView.from_issued(issued))


@router.get("/tokens")
async def list_agent_tokens(
    user: User = Depends(get_current_user),
    repo: PostgresAgentTokenRepository = Depends(get_agent_token_repository),
) -> ApiResponse[AgentTokenListResponse]:
    tokens = await repo.list_for_tenant(user.user_id)
    return ok(AgentTokenListResponse(tokens=[AgentTokenView.from_domain(t) for t in tokens]))


@router.post("/tokens/{token_id}:revoke")
async def revoke_agent_token(
    token_id: UUID,
    body: RevokeAgentTokenRequest,
    user: User = Depends(get_current_user),
    repo: PostgresAgentTokenRepository = Depends(get_agent_token_repository),
) -> ApiResponse[AgentTokenView]:
    revoked = await revoke_token(
        repo, tenant_id=user.user_id, token_id=token_id, reason=body.reason
    )
    return ok(AgentTokenView.from_domain(revoked))


@router.post("/tokens/{token_id}:rotate")
async def rotate_agent_token(
    token_id: UUID,
    body: RotateAgentTokenRequest,
    user: User = Depends(get_current_user),
    repo: PostgresAgentTokenRepository = Depends(get_agent_token_repository),
) -> ApiResponse[IssuedAgentTokenView]:
    old = await revoke_token(repo, tenant_id=user.user_id, token_id=token_id, reason="rotated")
    issued = await issue_token(
        repo,
        tenant_id=user.user_id,
        scopes=old.scopes,
        allow_instruments=old.allow_instruments,
        notional_cap=old.notional_cap,
        ttl=timedelta(seconds=body.ttl_seconds),
        now=datetime.now(timezone.utc),
    )
    return ok(IssuedAgentTokenView.from_issued(issued))


# --- proposals ----------------------------------------------------------------


@router.get("/proposals")
async def list_proposals(
    user: User = Depends(get_current_user),
    repo: PostgresProposalRepository = Depends(get_proposal_repository),
) -> ApiResponse[ProposalListResponse]:
    proposals = await repo.list_for_tenant(user.user_id)
    return ok(ProposalListResponse(proposals=list(proposals)))


@router.get("/proposals/{proposal_id}")
async def get_proposal(
    proposal_id: UUID,
    user: User = Depends(get_current_user),
    repo: PostgresProposalRepository = Depends(get_proposal_repository),
) -> ApiResponse[StrategyProposal]:
    proposal = await repo.get_for_tenant(proposal_id, tenant_id=user.user_id)
    if proposal is None:
        raise ProposalNotFoundError(str(proposal_id))
    return ok(proposal)


# --- experiments --------------------------------------------------------------


@router.get("/experiments/{experiment_id}")
async def get_experiment_view(
    experiment_id: UUID,
    user: User = Depends(get_current_user),
    repo: PostgresExperimentRepository = Depends(get_experiment_repository),
) -> ApiResponse[Experiment]:
    experiment = await get_experiment(repo, user.user_id, experiment_id)
    return ok(experiment)


@router.get("/experiments/{experiment_id}/lineage")
async def get_experiment_lineage(
    experiment_id: UUID,
    user: User = Depends(get_current_user),
    repo: PostgresExperimentRepository = Depends(get_experiment_repository),
) -> ApiResponse[list[Experiment]]:
    chain = await get_lineage_chain(repo, user.user_id, experiment_id)
    return ok(list(chain))


__all__ = [
    "get_agent_token_repository",
    "get_proposal_repository",
    "get_experiment_repository",
    "router",
]
