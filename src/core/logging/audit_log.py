"""7.4 — audit_log recording utility (FD-7.2).

Spec: 04_db_schema_v1.7.md (Audit Log, WORM), 01_data_models_v1.4.md#§1.6
(Decimal↔JSONB serialization principle),
docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.1(A) PLT-07
(trace_id column recording).

This function inserts into the audit_log table only — as a WORM table, no
UPDATE/DELETE paths should be created anywhere in this module (DB-level REVOKE
provides defense-in-depth, 04 §v1.6). Since db/session.py (SQLAlchemy async,
worktree 16) does not yet exist, asyncpg connections are accepted directly —
when the SQLAlchemy session layer is added later, it can reuse this logic by
passing the raw connection from that layer.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from src.core.observability.context import current as current_request_context
from src.data.models.serialization import DecimalSafeEncoder


async def record_audit_log(
    conn: asyncpg.Connection,
    *,
    actor_agent: str,
    action_type: str,
    decision_data: dict[str, Any],
    user_id: UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    verification_chain: dict[str, Any] | None = None,
    trace_id: UUID | None = None,
) -> None:
    """Safely serialize decision_data/verification_chain even if Decimal is
    mixed in (DecimalSafeEncoder, 01 §1.6) — to avoid precision loss, convert
    to string instead of float.

    If `trace_id` is not provided, use the value from the current request
    context (PLT-01 `src.core.observability.context.current()`) — callers do
    not need to carry trace_id directly, and correlation is preserved."""
    await conn.execute(
        """
        INSERT INTO audit_log
            (user_id, actor_agent, action_type, target_type, target_id,
             decision_data, verification_chain, trace_id)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8)
        """,
        user_id,
        actor_agent,
        action_type,
        target_type,
        target_id,
        json.dumps(decision_data, cls=DecimalSafeEncoder),
        json.dumps(verification_chain, cls=DecimalSafeEncoder)
        if verification_chain is not None
        else None,
        trace_id if trace_id is not None else current_request_context().trace_id,
    )
