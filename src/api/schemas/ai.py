"""AI-17 -- `src/api/routers/ai.py` request/response schemas.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.1 AI-17
("token issue/rotate/revoke, proposal list, experiment view -- human
session, login_required").

`AgentTokenView` wraps `domain/token_rules.py::AgentToken` (the dataclass
`adapters/postgres_token_repository.py` already returns) -- HTTP detail only
lives here, the same split `schemas/foundation/connections.py`'s own
docstring states. `secret` never appears on `AgentTokenView` (it is not a
column `agent_token` even stores, `application/issue_token.py`'s own
docstring) -- `IssuedAgentTokenView` is the once-only shape carrying it,
returned solely by the issue/rotate endpoints.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from src.foundation.ai.factory.contracts.v1 import StrategyProposal
from src.foundation.ai.gateway.application.issue_token import IssuedToken
from src.foundation.ai.gateway.domain.token_rules import AgentToken, Scope

__all__ = [
    "IssueAgentTokenRequest",
    "RotateAgentTokenRequest",
    "RevokeAgentTokenRequest",
    "AgentTokenView",
    "IssuedAgentTokenView",
    "AgentTokenListResponse",
    "ProposalListResponse",
]

_MAX_TTL_SECONDS = 30 * 24 * 3600  # 30 days -- an agent token is not meant to outlive a sprint


class IssueAgentTokenRequest(BaseModel):
    scopes: list[Scope]
    allow_instruments: list[str] = []
    notional_cap: Decimal
    ttl_seconds: int = Field(gt=0, le=_MAX_TTL_SECONDS)


class RotateAgentTokenRequest(BaseModel):
    ttl_seconds: int = Field(gt=0, le=_MAX_TTL_SECONDS)


class RevokeAgentTokenRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=50)


class AgentTokenView(BaseModel):
    token_id: UUID
    scopes: list[Scope]
    allow_instruments: list[str]
    notional_cap: Decimal
    expires_at: datetime
    paper_only: bool
    revoked_at: datetime | None

    @classmethod
    def from_domain(cls, token: AgentToken) -> AgentTokenView:
        return cls(
            token_id=token.token_id,
            scopes=sorted(token.scopes, key=lambda s: s.value),
            allow_instruments=sorted(token.allow_instruments),
            notional_cap=token.notional_cap,
            expires_at=token.expires_at,
            paper_only=token.paper_only,
            revoked_at=token.revoked_at,
        )


class IssuedAgentTokenView(AgentTokenView):
    secret: str

    @classmethod
    def from_issued(cls, issued: IssuedToken) -> IssuedAgentTokenView:
        base = AgentTokenView.from_domain(issued.token)
        return cls(secret=issued.secret, **base.model_dump())


class AgentTokenListResponse(BaseModel):
    tokens: list[AgentTokenView]


class ProposalListResponse(BaseModel):
    proposals: list[StrategyProposal]
