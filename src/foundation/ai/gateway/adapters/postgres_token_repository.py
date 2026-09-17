"""asyncpg implementation of `agent_token` -- AI-4.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.1 AI-4, §9 AI-4
DoD ("opaque storage, cross-tenant 404").

`get_token`/`get_by_hash` deliberately do not filter on `tenant_id` in the
WHERE clause (same convention as
`PostgresConnectionRepository.get_connection`,
[[src/foundation/connections/adapters/postgres_repository.py]]) -- if this
repository collapsed "exists but owned by another tenant" and "does not
exist" into the same result, `application/revoke_token.py` could not tell
them apart to raise the two distinct exceptions it needs. `revoke_token`
does the opposite: it pins `tenant_id` into the UPDATE's WHERE clause as a
second line of defense, so calling this method directly with an attacker's
tenant_id (bypassing the application layer's ownership check) still yields
zero affected rows on its own (same convention as
`transition_connection_state`).

`token_hash` is stored exactly as the caller (issue time) already computed
it as a sha256 hex digest -- this module carries no hashing logic itself
(single source of truth is `src.core.risk.hashing.sha256_hex`, see
[[src/foundation/ai/gateway/application/issue_token.py]]).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.foundation.ai.gateway.domain.token_rules import AgentToken, Scope

__all__ = ["PostgresAgentTokenRepository"]


def _row_to_token(row: asyncpg.Record) -> AgentToken:
    return AgentToken(
        token_id=row["token_id"],
        tenant_id=row["tenant_id"],
        scopes=frozenset(Scope(s) for s in row["scopes"]),
        allow_instruments=frozenset(row["allow_instruments"]),
        notional_cap=row["notional_cap"],
        expires_at=row["expires_at"],
        paper_only=row["paper_only"],
        revoked_at=row["revoked_at"],
    )


class PostgresAgentTokenRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert_token(
        self,
        *,
        tenant_id: UUID,
        token_hash: str,
        scopes: frozenset[Scope],
        allow_instruments: frozenset[str],
        notional_cap: Decimal,
        expires_at: datetime,
    ) -> AgentToken:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO agent_token "
                "(tenant_id, token_hash, scopes, allow_instruments, notional_cap, expires_at) "
                "VALUES ($1, $2, $3, $4, $5, $6) RETURNING *",
                tenant_id,
                token_hash,
                [s.value for s in scopes],
                list(allow_instruments),
                notional_cap,
                expires_at,
            )
        assert row is not None
        return _row_to_token(row)

    async def get_token(self, token_id: UUID) -> AgentToken | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM agent_token WHERE token_id = $1", token_id)
        return None if row is None else _row_to_token(row)

    async def get_by_hash(self, token_hash: str) -> AgentToken | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM agent_token WHERE token_hash = $1", token_hash)
        return None if row is None else _row_to_token(row)

    async def revoke_token(
        self, token_id: UUID, *, tenant_id: UUID, reason: str
    ) -> AgentToken | None:
        """Idempotent revoke -- if already revoked, the `revoked_at IS NULL`
        condition matches zero rows and the current state is silently
        re-fetched instead (same convention as `auth_session.revoke`:
        "expiry/revoke takes effect immediately" means a repeat request is a
        no-op, not an error).

        Returns `None` if the `(token_id, tenant_id)` combination does not
        exist at all (including "exists but owned by another tenant") -- the
        application layer only reaches this method after `get_token` has
        already confirmed existence/ownership, so `None` never surfaces on
        the normal path (cross-tenant 404 is the application layer's
        responsibility). But if that pre-check is bypassed and this method
        is called directly with an attacker's tenant_id, the fact that the
        target row could not be found is reflected as-is (another tenant's
        row is never returned by mistake)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE agent_token SET revoked_at = now(), revoke_reason = $3 "
                "WHERE token_id = $1 AND tenant_id = $2 AND revoked_at IS NULL "
                "RETURNING *",
                token_id,
                tenant_id,
                reason,
            )
            if row is None:
                row = await conn.fetchrow(
                    "SELECT * FROM agent_token WHERE token_id = $1 AND tenant_id = $2",
                    token_id,
                    tenant_id,
                )
        return None if row is None else _row_to_token(row)
