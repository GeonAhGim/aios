"""Agent Gateway token rules -- pure functions/value objects, no I/O.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md, table row AI-2
("Issue/verify/revoke rules (pure). No scope escalation, paper_only
invariant, expiry/revoke take effect immediately"). INVARIANTS.md I-06
(external AI/agent capability is granted only through a closed scope enum
and server-side, immediately-revocable tokens, separate from human sessions;
tokens can never reach a self-privilege-management API).

`contracts/v1.py` (AI-1, not yet implemented per docs/specs as of 2026-09-17
-- there is no src/foundation/ai/gateway/contracts/ directory) is where the
spec's `AgentToken`/`Scope` will eventually live as a public wire schema.
This module pre-implements that shape as a pure domain type, following the
same split the `connections` bounded context already uses between
`domain/models.py` (pure) and `contracts/v1.py` (pydantic wire schema) --
see src/foundation/connections/domain/models.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID


class Scope(str, Enum):
    """The full universe of scopes per spec §3 "scope semantics" -- exactly
    these four. LIVE, funds, policy, and token-management scopes do not
    exist (I-06, spec §3 "LIVE, funds, policy, and token-management scopes
    do not exist"). Adding a value requires a spec revision first."""

    READ = "read"
    RESEARCH = "research"
    PROPOSE = "propose"
    PAPER = "paper"


ALL_SCOPES: frozenset[Scope] = frozenset(Scope)


class TokenRuleError(Exception):
    """Common base for token rule violations -- the application layer maps
    these onto the spec §3 error catalog (AI_SCOPE_DENIED, AI_TOKEN_REVOKED,
    ...)."""


class ScopeEscalationError(TokenRuleError):
    """Requested scopes at issue/rotation time exceed what the issuer
    actually holds -- "no scope escalation"."""

    def __init__(self, requested: frozenset[Scope], grantable: frozenset[Scope]) -> None:
        self.requested = requested
        self.grantable = grantable
        overflow = sorted(s.value for s in requested - grantable)
        super().__init__(
            f"scope escalation: requested {overflow} exceeds grantable "
            f"{sorted(s.value for s in grantable)}"
        )


class TokenRevokedError(TokenRuleError):
    """Maps to AI_TOKEN_REVOKED (401)."""


class TokenExpiredError(TokenRuleError):
    """Spec §3's error catalog has no separate code for expiry, so it maps
    to the same 401 (AI_TOKEN_REVOKED) as revoke -- both mean "this token
    can no longer be used"."""


class ScopeDeniedError(TokenRuleError):
    """Maps to AI_SCOPE_DENIED (403) -- authorize() attempted with a scope
    the token was never granted."""

    def __init__(self, scope: Scope, granted: frozenset[Scope]) -> None:
        self.scope = scope
        self.granted = granted
        super().__init__(
            f"scope {scope.value!r} not granted (has {sorted(s.value for s in granted)})"
        )


class InstrumentNotAllowedError(TokenRuleError):
    """Maps to AI_SCOPE_DENIED (403) -- instrument outside the §2.1
    `allow_instruments` allowlist."""

    def __init__(self, instrument: str, allowed: frozenset[str]) -> None:
        self.instrument = instrument
        self.allowed = allowed
        super().__init__(f"instrument {instrument!r} not in allow_instruments {sorted(allowed)}")


class NotionalCapExceededError(TokenRuleError):
    """Maps to AI_SCOPE_DENIED (403) -- request exceeds §2.1 `notional_cap`."""

    def __init__(self, notional: Decimal, cap: Decimal) -> None:
        self.notional = notional
        self.cap = cap
        super().__init__(f"notional {notional} exceeds cap {cap}")


@dataclass(frozen=True)
class AgentToken:
    """Spec §2.1 table: `AgentToken{token_id, tenant_id, scopes,
    allow_instruments, notional_cap, expires_at, paper_only=True}`.
    `revoked_at` is a field this domain module adds to express "revoke takes
    effect immediately" -- the actual atomic revoke is performed by
    adapters/postgres_token_repository.py (AI-4, not yet implemented) via a
    standard-105 conditional UPDATE; this module only judges an
    already-revoked state."""

    token_id: UUID
    tenant_id: UUID
    scopes: frozenset[Scope]
    allow_instruments: frozenset[str]
    notional_cap: Decimal
    expires_at: datetime
    paper_only: bool = True
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        # Spec §2.1 "paper_only invariant" -- an AgentToken with
        # paper_only=False must be impossible to construct at the domain
        # layer (fail-closed at construction time; there is no setter to
        # flip it later, and the dataclass is frozen).
        if not self.paper_only:
            raise TokenRuleError(
                "paper_only invariant violated: AgentToken must be paper_only=True"
            )
        if not self.scopes:
            raise TokenRuleError("AgentToken must carry at least one scope")


def issue_scopes(requested: frozenset[Scope], grantable: frozenset[Scope]) -> frozenset[Scope]:
    """Reject a requested scope set at issue/rotation time if it exceeds
    `grantable` (what the issuer actually holds) -- "no scope escalation".
    An empty request is also rejected (the §2.1 table never defines a
    scopeless token)."""
    if not requested:
        raise TokenRuleError("at least one scope must be requested")
    if not requested <= grantable:
        raise ScopeEscalationError(requested, grantable)
    return requested


def is_expired(token: AgentToken, now: datetime) -> bool:
    return now >= token.expires_at


def is_revoked(token: AgentToken, now: datetime) -> bool:
    """Invalid the instant `revoked_at` is set (now >= revoked_at) -- no
    grace period, no caching (spec §2.1 "expiry/revoke take effect
    immediately"). Immediacy holds as long as the caller always supplies the
    freshest `revoked_at` (never caches it) -- that freshness guarantee is
    the adapters/application layer's responsibility, not this function's."""
    return token.revoked_at is not None and now >= token.revoked_at


def authorize(
    token: AgentToken,
    scope: Scope,
    now: datetime,
    *,
    instrument: str | None = None,
    notional: Decimal | None = None,
) -> None:
    """The pure judgment behind `application/authorize.py` (AI-4, not yet
    implemented), which spec §2.1 designates as "the single authorization
    point for every MCP tool". Returns None on success, raises on failure --
    the check order itself is part of the contract: liveness (revoke/expiry)
    is checked before scope, so a dead token carrying a scope that happens
    to match never gives the false impression that "the scope is fine"
    (fail-closed)."""
    if is_revoked(token, now):
        raise TokenRevokedError(f"token {token.token_id} revoked at {token.revoked_at}")
    if is_expired(token, now):
        raise TokenExpiredError(f"token {token.token_id} expired at {token.expires_at}")
    if scope not in token.scopes:
        raise ScopeDeniedError(scope, token.scopes)
    if (
        instrument is not None
        and token.allow_instruments
        and instrument not in token.allow_instruments
    ):
        raise InstrumentNotAllowedError(instrument, token.allow_instruments)
    if notional is not None and notional > token.notional_cap:
        raise NotionalCapExceededError(notional, token.notional_cap)
