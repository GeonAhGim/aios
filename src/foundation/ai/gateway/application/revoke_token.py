"""RevokeToken command -- AI-4.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.1 AI-4 DoD
("cross-tenant 404"), §2.1 AI-2 "revoke takes effect immediately".

Same two-layer defense as connections' `revoke_connection`
([[src/foundation/connections/application/revoke_connection.py]]):
1) `get_token` (no tenant filter) lets the application layer judge
   existence vs. ownership itself, telling "does not exist" apart from
   "owned by another tenant" (both collapse to 404 for the caller, but the
   service layer keeps them as distinct exceptions).
2) The actual UPDATE (`repo.revoke_token`) also pins `tenant_id` into its
   WHERE clause, so bypassing this layer's check and calling the repository
   directly is still blocked.
"""

from __future__ import annotations

from uuid import UUID

from src.foundation.ai.gateway.adapters.postgres_token_repository import (
    PostgresAgentTokenRepository,
)
from src.foundation.ai.gateway.application.errors import (
    AgentTokenNotFoundError,
    CrossTenantAgentTokenAccessError,
)
from src.foundation.ai.gateway.domain.token_rules import AgentToken


async def revoke_token(
    repo: PostgresAgentTokenRepository,
    *,
    tenant_id: UUID,
    token_id: UUID,
    reason: str,
) -> AgentToken:
    token = await repo.get_token(token_id)
    if token is None:
        raise AgentTokenNotFoundError(str(token_id))
    if token.tenant_id != tenant_id:
        raise CrossTenantAgentTokenAccessError(str(token_id))

    revoked = await repo.revoke_token(token_id, tenant_id=tenant_id, reason=reason)
    # get_token above already confirmed (token_id, tenant_id) exists together,
    # so repo.revoke_token cannot legitimately return None here.
    assert revoked is not None
    return revoked
