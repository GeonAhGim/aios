"""Authorize use case -- AI-4.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.1 "authorize(token,
scope, resource) is the single authorization point for every MCP tool".

This function is where I/O (hash lookup) meets AI-2's pure judgment
(`token_rules.authorize`). A token that cannot be found by hash (either it
does not exist, or the presented secret is wrong) has no dedicated error
code (spec §3's error catalog), so it fails closed the same way a revoked
token does -- 401 (`AI_TOKEN_REVOKED`) -- for the same reason AI-2's
`TokenExpiredError` maps expiry to that same 401 instead of inventing a new
code."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from src.foundation.ai.gateway.adapters.postgres_token_repository import (
    PostgresAgentTokenRepository,
)
from src.foundation.ai.gateway.application.issue_token import hash_token_secret
from src.foundation.ai.gateway.domain import token_rules
from src.foundation.ai.gateway.domain.token_rules import AgentToken, Scope


async def authorize(
    repo: PostgresAgentTokenRepository,
    *,
    token_secret: str,
    scope: Scope,
    now: datetime,
    instrument: str | None = None,
    notional: Decimal | None = None,
) -> AgentToken:
    token_hash = hash_token_secret(token_secret)
    token = await repo.get_by_hash(token_hash)
    if token is None:
        raise token_rules.TokenRevokedError("unknown token secret -- no matching agent_token hash")

    token_rules.authorize(token, scope, now, instrument=instrument, notional=notional)
    return token
