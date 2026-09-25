"""AI-8 -- generation schema (pure, no I/O).

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.3 AI-8
`domain/schema.py` ("generation schema (JSON Schema): script/hypothesis/
data_scope/params -- free text forbidden"), §3 "proposal schema"
("script_source (AIOS Script, grammar aios-script-1), hypothesis (<=2,000
chars), data_scope, params, expected_regime (optional). Unknown fields
rejected").

`ProposalDraft` is exactly what a `ModelProvider.generate()` call (AI-5
`ports/model_provider.py`) must return as `StructuredOutput.data` -- that
port's own docstring names this module as the schema owner ("validating
[the output] against the schema is the caller's job (AI-8's
`domain/schema.py`, not yet implemented)"). It is narrower than AI-8's
`contracts/v1.py::StrategyProposal`: an LLM/external agent never produces
`proposal_id`/`provider_ref`/`prompt_hash`/`created_by_token` -- those are
attached by the calling context (AI-9's `application/generate_proposal.py`,
not yet implemented) after this draft already validated.

This module reuses pydantic (already a dependency, already the wire-schema
mechanism every other AI leaf uses -- gateway/contracts/v1.py,
providers/ports/model_provider.py) instead of adding a `jsonschema` runtime
dependency (CLAUDE.md frequent mistake #8: a dependency swap/addition needs
an explicit architecture decision, not an implicit one made inside a single
leaf). `proposal_draft_json_schema()` gives the JSON Schema dict a provider
adapter passes to `ModelProvider.generate(schema=...)`; `ProposalDraft`
itself is also the runtime validator for whatever comes back.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from src.foundation.ai.factory.contracts.v1 import SCHEMA_VERSION, DataScope

__all__ = [
    "ProposalDraft",
    "ProposalSchemaError",
    "proposal_draft_json_schema",
    "validate_draft",
]


class ProposalSchemaError(Exception):
    """Raised when a candidate payload does not satisfy `ProposalDraft` --
    free text, an unknown field, or a malformed `data_scope`/`params`
    value. Maps to spec §3 `AI_PROPOSAL_SCHEMA` (400) at the application
    layer (AI-9, not yet implemented); this module only raises the pure
    rejection."""


class ProposalDraft(BaseModel):
    """§3's "proposal schema" verbatim, minus the fields the calling context
    attaches after validation. `extra="forbid"` is this module's
    implementation of "reject unknown fields" -- a payload with a field
    outside this exact set is rejected wholesale rather than silently
    dropped, so a
    field an AI channel invented (e.g. an out-of-band instruction smuggled
    as a fake key) can never reach `evaluate_proposal_candidate`
    (`domain/proposal_rules.py`) unnoticed. `frozen=True` matches every
    other AI contract/domain value object in this bounded context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    script_source: str
    hypothesis: str
    data_scope: DataScope
    params: dict[str, bool | int | float | str] = {}
    expected_regime: str | None = None
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


def proposal_draft_json_schema() -> dict[str, Any]:
    """The JSON Schema a provider adapter (AI-6/7/7b, `external_agent_provider`)
    passes as `ModelProvider.generate(schema=...)`'s `schema` argument."""
    return ProposalDraft.model_json_schema()


def validate_draft(payload: Mapping[str, Any]) -> ProposalDraft:
    """Validate a raw candidate payload (typically
    `StructuredOutput.data`, AI-5 `ports/model_provider.py`) against the
    generation schema. Raises `ProposalSchemaError` fail-closed on any
    violation -- free text, an unknown field, a wrong type, an empty
    `data_scope.instruments` -- rather than coercing or dropping the
    offending part."""
    try:
        return ProposalDraft.model_validate(payload)
    except ValidationError as exc:
        raise ProposalSchemaError(str(exc)) from exc
