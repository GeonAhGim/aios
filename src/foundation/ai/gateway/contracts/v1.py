"""AI-1 -- Agent Gateway contract v1.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.1 AI-1, §3, §4.

The gateway is how AI agents (built-in providers or external MCP clients such
as Claude Code / Codex CLI / Gemini CLI) act against AIOS without ever
inheriting a human session's authority (I-06). Two artifacts carry that
boundary end to end:

- `AgentToken`: a closed-scope credential. `scopes` is a subset of the four
  `Scope` values that exist -- §3 states plainly that no LIVE, funds, or
  policy-management scope exists to escalate into. `paper_only` defaults to
  `True`; AI-2 (`domain/token_rules.py`) owns making that default the only
  value issuance ever produces.
- `ConfirmTicket`: a one-time confirmation ticket binding a previewed action
  to its execution by `action_digest` (§3's confirmation-token contract).
  AI-2 (`domain/confirm.py`) owns single-use and TTL enforcement; this
  module only defines the shape.

`domain/token_rules.py` and `domain/confirm.py` import this module; this
module does not import them (standard 71 §4, the same layering as
FND-03/LB-1/RD-2). Adding a field is minor (standard 107, a default is
required) -- removing a field or changing its meaning requires a new `v2`
module.
"""

from __future__ import annotations

import enum
import re
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, field_validator

__all__ = [
    "SCHEMA_VERSION",
    "Scope",
    "AgentGatewayErrorCode",
    "AgentToken",
    "ConfirmTicket",
]

SCHEMA_VERSION: Literal["v1"] = "v1"

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _validate_sha256_hex(value: str) -> str:
    """Shape check only (64 lowercase hex chars) -- never recomputes a
    digest. Any new digest for this field must go through
    `src.core.risk.hashing.sha256_hex`/`canonical_json` (R-01); this module
    does not reimplement sha256 hashing."""
    if not _SHA256_HEX_RE.fullmatch(value):
        raise ValueError("must be a lowercase sha256 hex digest (64 hex chars)")
    return value


class Scope(str, enum.Enum):
    """§3's scope taxonomy verbatim -- inventing a fifth scope (e.g. LIVE or
    a token-management scope) is forbidden, this enum is the single source
    of truth (I-06)."""

    READ = "read"  # indicator/data/coverage lookups
    RESEARCH = "research"  # backtests, sweeps, experiment queries
    PROPOSE = "propose"  # submit a strategy proposal
    PAPER = "paper"  # request a PAPER execution (requires a ConfirmTicket)


class AgentGatewayErrorCode(str, enum.Enum):
    """§3's error taxonomy, gateway-owned subset only (token issuance/scope
    checks and confirm-ticket round-trips) -- the provider-budget and
    proposal-schema codes belong to AI-5/AI-8's contracts, not this file."""

    SCOPE_DENIED = "AI_SCOPE_DENIED"  # 403
    TOKEN_REVOKED = "AI_TOKEN_REVOKED"  # 401
    CONFIRM_REQUIRED = "AI_CONFIRM_REQUIRED"  # 428
    CONFIRM_MISMATCH = "AI_CONFIRM_MISMATCH"  # 409


class AgentToken(BaseModel, frozen=True):
    """A closed-scope credential (§2.1 AI-1 row verbatim field list).

    `frozen=True` mirrors `ComplianceDecision`/`RuleHit`
    (mandates/contracts/v1.py) -- a credential must not be tamperable in
    memory after construction; widening `.scopes` downstream must be a
    `ValidationError`, not a silent attribute assignment.

    `allow_instruments` empty means no instrument is authorized (fail-closed
    default, not "no restriction") -- AI-4's issuance use case is the only
    place that is allowed to leave it non-empty.
    """

    token_id: UUID
    tenant_id: UUID
    scopes: frozenset[Scope]
    allow_instruments: frozenset[str]
    notional_cap: Decimal
    expires_at: AwareDatetime
    paper_only: bool = True
    schema_version: Literal["v1"] = SCHEMA_VERSION


class ConfirmTicket(BaseModel, frozen=True):
    """A one-time confirmation ticket (§2.1 AI-1 row verbatim field list).

    `action_digest` is the digest of the previewed action; AI-2's
    `domain/confirm.py` is the only place that compares it against the
    execution-time digest and enforces single use.
    """

    ticket_id: UUID
    action_digest: str = Field(min_length=64, max_length=64, pattern=_SHA256_HEX_RE.pattern)
    expires_at: AwareDatetime
    schema_version: Literal["v1"] = SCHEMA_VERSION

    @field_validator("action_digest")
    @classmethod
    def _check_digest(cls, value: str) -> str:
        return _validate_sha256_hex(value)
