"""AIOS Script compatibility package (§2.4/§9.4 DSL-10) — `cond_v2_bridge.py` bridges
legacy cond-v2 conditionals to DSL-1 AST. New package, `src/core/**` SCAFFOLD zone. Pure
(no I/O). `src/core/strategy/**` (FROZEN_PAPER_ONLY) is read-only — do not modify.
Consumers: DSL-11 `script_facade.py`, artifact `compat_map` field.
"""
from __future__ import annotations

from src.core.script.compat.cond_v2_bridge import (
    COMPAT_SCHEMA,
    SIGNAL_NAME,
    SOURCE_GRAMMAR_VERSION,
    BridgedScript,
    CompiledCondV2,
    CondV2BridgeError,
    bridge_cond_v2,
    canonical_expression,
    compile_cond_v2,
    node_hash,
)

__all__ = [
    "COMPAT_SCHEMA",
    "SIGNAL_NAME",
    "SOURCE_GRAMMAR_VERSION",
    "BridgedScript",
    "CompiledCondV2",
    "CondV2BridgeError",
    "bridge_cond_v2",
    "canonical_expression",
    "compile_cond_v2",
    "node_hash",
]
