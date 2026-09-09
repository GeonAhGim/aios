"""L4_strategy_portfolio_backtest_v1.0.md §2 row 158 / §9 L36 --
content-addressed strategy artifact and tamper detection.

`artifact_hash` binds the strategy's compiled shape to the exact
grammar/registry/compiler versions it was built under (I-04: "a
strategy artifact is content-addressed and immutable once versioned").
Recomputing the hash from an artifact's own stored fields and comparing
it to the stored `artifact_hash` (`verify`) is how a single tampered
field -- someone hand-editing `fsm_definition` after the fact -- gets
caught deterministically, without touching a database.

L05 (`src.core.strategy.condition_ast`) is FROZEN_PAPER_ONLY and was
never built (§A permanent non-assignment), so `GRAMMAR_VERSION` is taken
from its sanctioned substitute, DSL-11's script facade grammar
(`src.core.script.grammar.ast`), per this leaf's task note. Likewise
`condition_compiler.COMPILER_VERSION` from the original spec text was
never built; `compiler_version` is accepted here as an opaque caller
-supplied string so a future compiler (DSL-11's `compile_artifact.py`
sibling leaf) can supply its own version without this module changing.

`registry_version` is not a separate constant -- it *is* the value
returned by L02 `IndicatorRegistry.registry_hash()`, delegated to
verbatim (no local reimplementation of indicator-spec hashing).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

from src.core.indicators.registry import DEFAULT_REGISTRY, IndicatorRegistry
from src.core.risk.hashing import canonical_json, sha256_hex
from src.core.script.grammar.ast import GRAMMAR_VERSION

ARTIFACT_HASH_MISMATCH = "INTEGRITY_ARTIFACT_HASH_MISMATCH"


class StrategyArtifact(BaseModel):
    """Fixed field set per §2 row 158 -- content-addressed and immutable
    (`frozen=True`); a new value always means a new artifact, never an
    in-place edit."""

    model_config = ConfigDict(frozen=True)

    artifact_hash: str
    strategy_id: str
    version: str
    compiler_version: str
    grammar_version: str
    registry_version: str
    fsm_definition: dict[str, Any]


def _compute_artifact_hash(
    *,
    fsm_definition: Mapping[str, Any],
    grammar_version: str,
    registry_version: str,
    compiler_version: str,
) -> str:
    """sha256 over the canonical-JSON payload of the four hash inputs
    (§2 row 158). The inputs are combined as named fields of one R-01
    `canonical_json` payload rather than raw byte concatenation, so a
    value crossing a field boundary (e.g. part of `fsm_definition`
    bleeding into `compiler_version`) can never collide with a
    differently-shaped input."""
    payload = {
        "fsm_definition": dict(fsm_definition),
        "grammar_version": grammar_version,
        "registry_version": registry_version,
        "compiler_version": compiler_version,
    }
    return sha256_hex(canonical_json(payload))


def build_artifact(
    *,
    strategy_id: str,
    version: str,
    fsm_definition: Mapping[str, Any],
    compiler_version: str,
    registry: IndicatorRegistry = DEFAULT_REGISTRY,
    grammar_version: str = GRAMMAR_VERSION,
) -> StrategyArtifact:
    """Build a content-addressed `StrategyArtifact`. `registry_version` is
    always `registry.registry_hash()` -- callers cannot pass their own
    registry hash, so the artifact can never claim a registry state it
    wasn't actually built against."""
    registry_version = registry.registry_hash()
    artifact_hash = _compute_artifact_hash(
        fsm_definition=fsm_definition,
        grammar_version=grammar_version,
        registry_version=registry_version,
        compiler_version=compiler_version,
    )
    return StrategyArtifact(
        artifact_hash=artifact_hash,
        strategy_id=strategy_id,
        version=version,
        compiler_version=compiler_version,
        grammar_version=grammar_version,
        registry_version=registry_version,
        fsm_definition=dict(fsm_definition),
    )


def verify(artifact: StrategyArtifact) -> str | None:
    """Recompute `artifact_hash` from `artifact`'s own stored fields and
    compare. Returns `None` for an untampered artifact, or
    `"INTEGRITY_ARTIFACT_HASH_MISMATCH"` if any of `fsm_definition`,
    `grammar_version`, `registry_version`, `compiler_version` was changed
    after the artifact was built."""
    recomputed = _compute_artifact_hash(
        fsm_definition=artifact.fsm_definition,
        grammar_version=artifact.grammar_version,
        registry_version=artifact.registry_version,
        compiler_version=artifact.compiler_version,
    )
    if recomputed != artifact.artifact_hash:
        return ARTIFACT_HASH_MISMATCH
    return None


__all__ = ["ARTIFACT_HASH_MISMATCH", "StrategyArtifact", "build_artifact", "verify"]
