"""AI-8 -- Strategy Factory contract v1.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.3 AI-8, §3, §4.

`StrategyProposal{proposal_id, script_source, hypothesis, data_scope{instruments,
tf, span}, params, provider_ref, prompt_hash, created_by_token}` is the spec's
verbatim field list -- everything an AI proposal channel (a built-in provider,
AI-6/7/7b, or an external MCP client, AI-2.2's `external_agent_provider.py`)
produces plus what AI-9's `application/generate_proposal.py` (not yet
implemented) attaches when it stores the proposal. The LLM/agent itself never
fills `proposal_id`/`provider_ref`/`prompt_hash`/`created_by_token` directly --
those come from `domain/schema.py`'s narrower `ProposalDraft` plus the calling
context, not from model output (`domain/schema.py`'s docstring covers that
split).

`ProposalOutcome` is `domain/proposal_rules.py`'s acceptance/rejection verdict
for a proposal candidate (schema/compile/data-coverage/forbidden-API, this
leaf's four checks) -- it is not AI-12's later validation-pipeline (L36-L44)
result, which is a separate contract that leaf will define when implemented.

`ProposalEvaluation` is that AI-12 contract (`application/evaluate_proposal.py`).
It carries `src.foundation.validation.domain.rules::evaluate_bundle`'s verdict
for an already-accepted `StrategyProposal` -- `accepted`/`hard_fail_reasons`
mirror `evaluate_bundle`'s own "FAIL iff hard_fail_reasons non-empty" (I-07)
biconditional exactly, the same discipline `ProposalOutcome._check_consistency`
already applies one stage earlier in this pipeline.

`domain/token_rules.py`/`domain/confirm.py` do not import this module (they
predate AI-1 consolidation, task-2643 note dated 2026-09-17); this module
follows the same "contracts is the base layer" rule for AI-8's own domain
modules (`domain/schema.py`, `domain/proposal_rules.py` import this module,
not the reverse).
"""

from __future__ import annotations

import enum
import re
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, field_validator, model_validator

from src.foundation.market_data.contracts.v1 import Timeframe

__all__ = [
    "SCHEMA_VERSION",
    "ProviderRef",
    "ProposalRejectionReason",
    "DataScope",
    "StrategyProposal",
    "ProposalOutcome",
    "ProposalEvaluation",
]

SCHEMA_VERSION: Literal["v1"] = "v1"

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _validate_sha256_hex(value: str) -> str:
    """Shape check only -- never recomputes a digest (same discipline as
    `gateway/contracts/v1.py::_validate_sha256_hex`, duplicated here rather
    than imported because the two contracts modules must not depend on each
    other, standard 71 §4 layering)."""
    if not _SHA256_HEX_RE.fullmatch(value):
        raise ValueError("must be a lowercase sha256 hex digest (64 hex chars)")
    return value


class ProviderRef(str, enum.Enum):
    """Which channel produced the proposal -- §2.2's four adapters
    (anthropic_provider/openai_compatible_provider/gemini_provider) plus the
    non-adapter "proposal channel" (external_agent_provider: any MCP client,
    e.g. Claude Code/Codex CLI/Gemini CLI, attached with an agent token).
    Not a `ModelProvider` -- §2.2 states the pipeline is complete even
    without a built-in model."""

    ANTHROPIC = "anthropic"
    OPENAI_COMPATIBLE = "openai_compatible"
    GEMINI = "gemini"
    EXTERNAL_AGENT = "external_agent"


class ProposalRejectionReason(str, enum.Enum):
    """The four `domain/proposal_rules.py` acceptance checks, in the order
    `evaluate_proposal_candidate` runs them. Wire-level `AI_PROPOSAL_SCHEMA`/
    `AI_PROPOSAL_COMPILE` (spec §3) mapping is the application layer's job
    (AI-9, not yet implemented) -- this enum is the pure-domain reason a
    `ProposalOutcome` carries, not the HTTP error code itself."""

    SCHEMA_INVALID = "schema_invalid"
    COMPILE_FAILED = "compile_failed"
    DATA_SCOPE_UNCOVERED = "data_scope_uncovered"
    FORBIDDEN_API = "forbidden_api"


class DataScope(BaseModel, frozen=True):
    """§2.3 AI-8 row verbatim: `data_scope{instruments, tf, span}`.

    `instruments` is a set of instrument identifiers, the same
    `frozenset[str]` shape as `AgentToken.allow_instruments`
    (gateway/contracts/v1.py) -- both are opaque identifier strings compared
    for equality against `CoverageSpan.instrument_id`
    (market_data/contracts/v2/coverage.py), not resolved to a full
    `Instrument` record here (that resolution, if needed, is an application
    concern). `span` is a half-open `[start, end)` pair, matching
    `CoverageSpan`'s own half-open convention so "data scope within
    coverage" (`domain/proposal_rules.py::check_data_scope_covered`) is a
    plain interval-containment check.
    """

    instruments: frozenset[str]
    tf: Timeframe
    span: tuple[AwareDatetime, AwareDatetime]
    schema_version: Literal["v1"] = SCHEMA_VERSION

    @model_validator(mode="after")
    def _check_scope(self) -> DataScope:
        if not self.instruments:
            raise ValueError("data_scope.instruments must not be empty")
        start, end = self.span
        if end <= start:
            raise ValueError(f"data_scope.span must be start < end (got {start!r}, {end!r})")
        return self


class StrategyProposal(BaseModel, frozen=True):
    """§2.3 AI-8 row verbatim field list.

    `params` values are restricted to JSON scalars (`bool | int | float |
    str`) -- spec §3's "reject unknown fields"/"free text forbidden" rules
    bar smuggling another script or an arbitrary nested object through a
    parameter value;
    every parameter a script actually consumes is itself a scalar `input`
    declaration (DSL-1 grammar). `bool` is listed before `int` in the union
    so pydantic's smart-union mode does not coerce a `True`/`False` value
    into `1`/`0`.
    """

    proposal_id: UUID
    script_source: str
    hypothesis: str
    data_scope: DataScope
    params: dict[str, bool | int | float | str] = {}
    provider_ref: ProviderRef
    prompt_hash: str
    created_by_token: UUID
    schema_version: Literal["v1"] = SCHEMA_VERSION

    @field_validator("script_source")
    @classmethod
    def _check_script_source(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("script_source must not be empty")
        return value

    @field_validator("hypothesis")
    @classmethod
    def _check_hypothesis(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("hypothesis must not be empty")
        if len(value) > 2000:
            raise ValueError("hypothesis must be <= 2000 chars (spec §3)")
        return value

    @field_validator("prompt_hash")
    @classmethod
    def _check_prompt_hash(cls, value: str) -> str:
        return _validate_sha256_hex(value)


class ProposalOutcome(BaseModel, frozen=True):
    """The verdict `domain/proposal_rules.py::evaluate_proposal_candidate`
    (or a rejection raised partway through it) produces for one proposal
    candidate. `accepted`/`rejection_reason`/`script_hash` are kept mutually
    consistent by construction (`_check_consistency`) so a caller can never
    persist an `accepted=True` outcome that also carries a rejection reason,
    or vice versa -- the same "no silently contradictory state" discipline
    as `AgentToken.paper_only` (gateway/domain/token_rules.py)."""

    proposal_id: UUID
    accepted: bool
    rejection_reason: ProposalRejectionReason | None = None
    script_hash: str | None = None
    detail: str = ""
    schema_version: Literal["v1"] = SCHEMA_VERSION

    @model_validator(mode="after")
    def _check_consistency(self) -> ProposalOutcome:
        if self.accepted:
            if self.rejection_reason is not None:
                raise ValueError("an accepted outcome must not carry a rejection_reason")
            if self.script_hash is None:
                raise ValueError("an accepted outcome must carry a script_hash")
        else:
            if self.rejection_reason is None:
                raise ValueError("a rejected outcome must carry a rejection_reason")
            if self.script_hash is not None:
                raise ValueError("a rejected outcome must not carry a script_hash")
        return self


class ProposalEvaluation(BaseModel, frozen=True):
    """AI-12 -- `application/evaluate_proposal.py`'s verdict for an already
    -accepted `StrategyProposal`, produced by delegating to
    `src.foundation.validation.domain.rules::evaluate_bundle` (L42) over a
    caller-supplied `CheckResult` sequence. `accepted`/`hard_fail_reasons`
    are kept mutually consistent by construction (`_check_consistency`),
    mirroring `evaluate_bundle`'s own `_decide` biconditional (I-07: outcome
    is FAIL iff `hard_fail_reasons` is non-empty) -- the same "no silently
    contradictory state" discipline `ProposalOutcome._check_consistency`
    already applies one stage earlier in this pipeline."""

    proposal_id: UUID
    experiment_id: UUID
    accepted: bool
    hard_fail_reasons: tuple[str, ...] = ()
    obligations: tuple[str, ...] = ()
    metrics: dict[str, Any] = {}
    schema_version: Literal["v1"] = SCHEMA_VERSION

    @model_validator(mode="after")
    def _check_consistency(self) -> ProposalEvaluation:
        if self.accepted and self.hard_fail_reasons:
            raise ValueError("an accepted evaluation must not carry hard_fail_reasons")
        if not self.accepted and not self.hard_fail_reasons:
            raise ValueError("a rejected evaluation must carry at least one hard_fail_reason")
        return self
