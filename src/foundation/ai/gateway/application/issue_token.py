"""IssueToken command -- AI-4.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.1 AI-4, §3
("token: opaque 32 bytes, the server stores only sha256").

The plaintext opaque secret exists only in this function's return value --
the caller (AI-17 `api/routers/ai.py`, not yet implemented) must discard it
immediately after the response (only `token_hash` persists in the DB, the
same pattern as PLT-23's refresh token).

`issue_scopes` (AI-2, pure) checks only that the requested scopes do not
exceed the entire grantable universe -- AI-4 does not yet have a notion of
"the issuer's own held scopes" (the issuer will be a human session wired by
AI-17; that authorization belongs to the router's `login_required`).
`AgentToken.__post_init__` additionally enforces the paper_only/scopes
invariants in the domain layer (defense in depth; the migration's CHECK
constraint is the third line of defense at the DB layer)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from src.core.risk.hashing import sha256_hex
from src.foundation.ai.gateway.adapters.postgres_token_repository import (
    PostgresAgentTokenRepository,
)
from src.foundation.ai.gateway.domain.token_rules import ALL_SCOPES, AgentToken, Scope, issue_scopes

_SECRET_BYTES = 32


@dataclass(frozen=True)
class IssuedToken:
    """Issuance response -- `secret` exists only here and is never looked up
    again anywhere else."""

    secret: str
    token: AgentToken


def hash_token_secret(secret: str) -> str:
    """sha256 hex (64 chars) to store in the DB. `authorize.py` uses the same
    function to verify (issuance and verification never diverge on hashing
    logic -- a single source of truth)."""
    return sha256_hex(secret.encode("ascii"))


async def issue_token(
    repo: PostgresAgentTokenRepository,
    *,
    tenant_id: UUID,
    scopes: frozenset[Scope],
    allow_instruments: frozenset[str],
    notional_cap: Decimal,
    ttl: timedelta,
    now: datetime,
) -> IssuedToken:
    granted = issue_scopes(scopes, ALL_SCOPES)
    secret = secrets.token_bytes(_SECRET_BYTES).hex()
    token_hash = hash_token_secret(secret)
    expires_at = now + ttl

    token = await repo.insert_token(
        tenant_id=tenant_id,
        token_hash=token_hash,
        scopes=granted,
        allow_instruments=allow_instruments,
        notional_cap=notional_cap,
        expires_at=expires_at,
    )
    return IssuedToken(secret=secret, token=token)
