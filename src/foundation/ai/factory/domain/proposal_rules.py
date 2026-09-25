"""AI-8 -- proposal acceptance rules (pure, no I/O).

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.3 AI-8
`domain/proposal_rules.py` ("proposal acceptance rules (pure): schema pass,
compile pass, data scope within coverage, no forbidden API"), §9 AI-8 DoD
("reject free-form code"), §1 "deterministic gate" (a proposal must pass
compile/validation/risk gates in code, a prompt threshold is not a gate).

Four checks, in the order `evaluate_proposal_candidate` runs them (each
assumes the previous one already held, the same discipline as
`gateway/domain/token_rules.py::authorize`):

1. schema (`domain/schema.py::validate_draft`) -- reject free text/unknown
   fields before anything else runs.
2. DSL-12 compile (`src/core/script/artifact/compile.py::compile_source`)
   -- reject a script that does not compile, with the DSL error's own
   (line, col) (spec §3 `AI_PROPOSAL_COMPILE`).
3. data-scope coverage -- every instrument in `data_scope` must have
   registered `CoverageSpan`s (market_data DC-6) that fully contain
   `data_scope.span`.
4. no forbidden call namespace -- `compile_source`'s own type checker
   (`src/core/script/typing/checker.py::_infer_call`) type-checks *any*
   `ns.ident(...)` call whose arguments are numeric, regardless of whether
   `ns` is an actually-registered builtin namespace (that module's own
   documented "unverified" gap: per-function signatures for `ns.ident(...)`
   calls are unknown because the IND registry does not exist yet) -- a
   compiled script can still contain a call to an invented namespace. This
   is the only fail-closed gate against that, checked against
   `ALLOWED_CALL_NAMESPACES`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.core.script.artifact.compile import CompiledScript, ScriptCompileError, compile_source
from src.core.script.ir.ops import Call
from src.foundation.ai.factory.contracts.v1 import DataScope
from src.foundation.ai.factory.domain.schema import (
    ProposalDraft,
    ProposalSchemaError,
    validate_draft,
)
from src.foundation.market_data.api import merge_spans
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan

__all__ = [
    "ALLOWED_CALL_NAMESPACES",
    "ProposalRuleError",
    "ProposalSchemaRejectedError",
    "ProposalCompileRejectedError",
    "ProposalDataScopeUncoveredError",
    "ProposalForbiddenApiError",
    "validate_schema",
    "compile_script",
    "check_data_scope_covered",
    "check_no_forbidden_calls",
    "evaluate_proposal_candidate",
]

ALLOWED_CALL_NAMESPACES: frozenset[str] = frozenset({"math", "ta"})
"""The only two builtin call namespaces AIOS Script actually registers at
runtime: `src/core/script/runtime/builtins_math.py` (`MATH_BUILTINS`,
keyed `("math", ident)`) and `src/core/script/runtime/builtins_ta.py`
(`_NS = "ta"`). Duplicated here as a small, stable literal rather than
imported from the runtime module -- this domain module stays decoupled
from the interpreter's registry-loading machinery (`DEFAULT_REGISTRY`,
`IncrementalIndicator`), and a real namespace addition is rare/deliberate
enough that a test asserting these two values stay in sync with the
runtime module (see `tests/foundation/unit/ai/factory/test_proposal_rules.py`)
is adequate drift protection."""


class ProposalRuleError(Exception):
    """Common base for proposal acceptance rule violations. Each subclass
    maps to one `contracts.v1.ProposalRejectionReason` value; the wire-level
    spec §3 error code (`AI_PROPOSAL_SCHEMA`/`AI_PROPOSAL_COMPILE`) mapping
    is the application layer's job (AI-9, not yet implemented)."""


class ProposalSchemaRejectedError(ProposalRuleError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"proposal schema rejected: {reason}")


class ProposalCompileRejectedError(ProposalRuleError):
    """Wraps DSL-12's `ScriptCompileError`, carrying its taxonomy code and
    (line, col) through unchanged -- spec §3 `AI_PROPOSAL_COMPILE(400,
    includes DSL error location)`."""

    def __init__(self, compile_error: ScriptCompileError) -> None:
        self.code = compile_error.code
        self.line = compile_error.line
        self.col = compile_error.col
        self.message = compile_error.message
        super().__init__(str(compile_error))


class ProposalDataScopeUncoveredError(ProposalRuleError):
    def __init__(self, instrument_id: str) -> None:
        self.instrument_id = instrument_id
        super().__init__(f"data_scope not covered for instrument {instrument_id!r}")


class ProposalForbiddenApiError(ProposalRuleError):
    def __init__(self, ns: str, ident: str) -> None:
        self.ns = ns
        self.ident = ident
        super().__init__(
            f"call to disallowed namespace {ns}.{ident!r} "
            f"(allowed: {sorted(ALLOWED_CALL_NAMESPACES)})"
        )


def validate_schema(payload: Mapping[str, Any]) -> ProposalDraft:
    """Check 1. Delegates entirely to `domain/schema.py::validate_draft` --
    this function only re-raises as this module's own error type so callers
    catching `ProposalRuleError` see a single hierarchy."""
    try:
        return validate_draft(payload)
    except ProposalSchemaError as exc:
        raise ProposalSchemaRejectedError(str(exc)) from exc


def compile_script(source: str, *, registry_version: str) -> CompiledScript:
    """Check 2. Delegates entirely to DSL-12's `compile_source` -- this
    function never reimplements lexing/parsing/type-checking, it only
    re-raises `ScriptCompileError` as this module's own error type."""
    try:
        return compile_source(source, registry_version=registry_version)
    except ScriptCompileError as exc:
        raise ProposalCompileRejectedError(exc) from exc


def check_data_scope_covered(data_scope: DataScope, coverage_spans: Sequence[CoverageSpan]) -> None:
    """Check 3. For every instrument in `data_scope.instruments`, the
    registered `CoverageSpan`s for that instrument+timeframe (merged via
    DC-6 `merge_spans`) must contain a span that fully covers
    `data_scope.span` (`[start, end)`, half-open, matching `CoverageSpan`'s
    own convention). Raises fail-closed on the first uncovered instrument
    (deterministic order: `sorted()`, so which instrument is named in the
    error never depends on set iteration order)."""
    span_start, span_end = data_scope.span
    for instrument_id in sorted(data_scope.instruments):
        matching = [
            span
            for span in coverage_spans
            if span.instrument_id == instrument_id and span.timeframe == data_scope.tf
        ]
        merged = merge_spans(matching)
        covered = any(span.start_at <= span_start and span.end_at >= span_end for span in merged)
        if not covered:
            raise ProposalDataScopeUncoveredError(instrument_id)


def check_no_forbidden_calls(compiled: CompiledScript) -> None:
    """Check 4. Scans the compiled IR's instruction stream (not the raw
    source text -- the IR is what actually executes) for `Call` ops whose
    `ns` is outside `ALLOWED_CALL_NAMESPACES`. Deterministic: `instrs` is a
    fixed tuple in source order, so the first offending call is always the
    one reported."""
    for instr in compiled.ir.instrs:
        if isinstance(instr, Call) and instr.ns not in ALLOWED_CALL_NAMESPACES:
            raise ProposalForbiddenApiError(instr.ns, instr.ident)


def evaluate_proposal_candidate(
    *,
    payload: Mapping[str, Any],
    coverage_spans: Sequence[CoverageSpan],
    registry_version: str,
) -> tuple[ProposalDraft, CompiledScript]:
    """The single ordered pipeline AI-9's `application/generate_proposal.py`
    (not yet implemented) must call: schema -> compile -> data coverage ->
    forbidden API. Returns `(draft, compiled)` on full acceptance; raises
    the first `ProposalRuleError` encountered otherwise. Building the
    accepted `ProposalOutcome`/`StrategyProposal` from the return value
    (attaching `proposal_id`/`provider_ref`/`prompt_hash`/
    `created_by_token`) is that application layer's job, not this
    function's -- this module stays pure (no ids, no clock, no storage)."""
    draft = validate_schema(payload)
    compiled = compile_script(draft.script_source, registry_version=registry_version)
    check_data_scope_covered(draft.data_scope, coverage_spans)
    check_no_forbidden_calls(compiled)
    return draft, compiled
