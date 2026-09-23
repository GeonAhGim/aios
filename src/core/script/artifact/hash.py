"""L4_analytics_authoring_backtest_marketplace_v1.0.md §2.4 row 90 / §9.4 DSL-12 —
`script_hash`: source, IR, indicator registry version, grammar version -> content hash.

Pure function (no I/O). Serializes the four inputs into canonical JSON
(`sort_keys`, fixed separators, `ensure_ascii`) and produces a sha256 hex
digest (64 chars). The IR reuses DSL-7 `to_bytes()` (deterministic
serialization) as-is, so the "same AST = same IR bytes" contract carries
through to "same IR = same script_hash".

Why four inputs (§3.3, §3.4): `ta.*` is pinned to the IND registry version,
so the same source can yield a different artifact once the registry changes
— hence `registry_version` takes IND-1 `IndicatorRegistry.registry_hash()`
(the spec's canonical hash). The grammar version also lives inside the IR
(`IRProgram.grammar_version`), but is repeated at the payload's top level so
the hash-input contract stays legible even if the IR serialization format
changes. The source is not normalized (whitespace-only differences are a
different script artifact — the marketplace (MP-1) publishes the source
verbatim, so the source bytes are the identity). The reproduction key (BT-9)
uses this value as its first component.

Fail-closed: an empty source, empty registry version, or a non-IRProgram
value is rejected with `ValueError` — a plausible-looking hash is never
built from bad input.
"""
from __future__ import annotations

import hashlib
import json
from typing import Final

from src.core.script.grammar.ast import GRAMMAR_VERSION
from src.core.script.ir.ops import IRProgram, to_bytes

HASH_ALGORITHM: Final = "sha256"
HASH_SCHEMA: Final = "script-hash-1"


def hash_payload(
    *,
    source: str,
    ir: IRProgram,
    registry_version: str,
    grammar_version: str = GRAMMAR_VERSION,
) -> dict[str, str]:
    """Validates the four hash inputs, builds the canonical payload dict. Public for tests."""
    if not isinstance(source, str) or not source.strip():
        raise ValueError("script_hash: 소스가 비어 있습니다")
    if not isinstance(registry_version, str) or not registry_version:
        raise ValueError("script_hash: 레지스트리 버전이 비어 있습니다")
    if not isinstance(grammar_version, str) or not grammar_version:
        raise ValueError("script_hash: 문법 버전이 비어 있습니다")
    if not isinstance(ir, IRProgram):
        raise ValueError(f"script_hash: IRProgram이 아닙니다: {type(ir).__name__}")
    return {
        "schema": HASH_SCHEMA,
        "grammar_version": grammar_version,
        "ir_version": ir.ir_version,
        "ir": to_bytes(ir).decode("utf-8"),
        "registry_version": registry_version,
        "source": source,
    }


def script_hash(
    *,
    source: str,
    ir: IRProgram,
    registry_version: str,
    grammar_version: str = GRAMMAR_VERSION,
) -> str:
    """source/IR/registry/grammar version -> sha256 hex (64 chars). Same input = same value."""
    payload = hash_payload(
        source=source,
        ir=ir,
        registry_version=registry_version,
        grammar_version=grammar_version,
    )
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = ["HASH_ALGORITHM", "HASH_SCHEMA", "hash_payload", "script_hash"]
