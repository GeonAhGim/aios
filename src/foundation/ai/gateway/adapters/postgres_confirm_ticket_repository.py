"""asyncpg implementation of `confirm_ticket` -- AI-16.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.1 AI-4
(`ConfirmTicket`), §9 AI-16 DoD ("confirmation token round trip"),
INVARIANTS.md I-11. Backs the `ConfirmTicketRepository` Protocol
`application/promote_to_paper.py` (AI-13) already defines -- that module's
own docstring names this leaf as the one shipping the concrete adapter.

`issue()` is this adapter's own addition, not part of the AI-13 Protocol --
AI-13's `promote_to_paper` only ever reads an already-issued ticket
(`get`/`mark_consumed`); something has to create the row at preview time,
and `src/api/mcp/tools_paper.py`'s `preview_promotion` tool is that caller.

`mark_consumed`'s `WHERE consumed_at IS NULL` is the standard-105 single-use
guarantee under concurrency (two requests racing to consume the same ticket
-- only one `UPDATE` can match). Pinning `action_digest = $2` into the same
WHERE clause is defense in depth, not a second decision: `promote_to_paper`
already called `domain/confirm.py::verify_and_consume` (the pure digest-
match judgment) immediately before calling this method, so by the time this
UPDATE runs the digest is already known to match the row this connection
last read -- pinning it here only guards against that row having changed
between the `get()` and this call (the same "second line of defense"
`postgres_token_repository.py::revoke_token` already documents for pinning
`tenant_id` into its own conditional UPDATE).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from src.foundation.ai.gateway.domain.confirm import ConfirmTicket

__all__ = ["PostgresConfirmTicketRepository"]


def _row_to_ticket(row: asyncpg.Record) -> ConfirmTicket:
    return ConfirmTicket(
        ticket_id=row["ticket_id"],
        action_digest=row["action_digest"],
        expires_at=row["expires_at"],
        consumed_at=row["consumed_at"],
    )


class PostgresConfirmTicketRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def issue(
        self,
        *,
        tenant_id: UUID,
        created_by_token: UUID,
        action_digest: str,
        expires_at: datetime,
    ) -> ConfirmTicket:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO confirm_ticket "
                "(tenant_id, created_by_token, action_digest, expires_at) "
                "VALUES ($1, $2, $3, $4) RETURNING *",
                tenant_id,
                created_by_token,
                action_digest,
                expires_at,
            )
        assert row is not None
        return _row_to_ticket(row)

    async def get(self, ticket_id: UUID) -> ConfirmTicket | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM confirm_ticket WHERE ticket_id = $1", ticket_id
            )
        return None if row is None else _row_to_ticket(row)

    async def mark_consumed(
        self, ticket_id: UUID, *, execute_digest: str, now: datetime
    ) -> ConfirmTicket | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE confirm_ticket SET consumed_at = $3 "
                "WHERE ticket_id = $1 AND action_digest = $2 AND consumed_at IS NULL "
                "RETURNING *",
                ticket_id,
                execute_digest,
                now,
            )
        return None if row is None else _row_to_ticket(row)
