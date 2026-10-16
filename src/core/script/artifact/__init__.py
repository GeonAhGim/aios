"""AIOS Script artifact package (§9.4 DSL-12) — `hash.py`(script_hash) and
`compile.py`(DSL-2→7 pipeline assembly + §3.3 four error types/positions).

New package, `src/core/**` SCAFFOLD zone. Pure (no I/O). Consumers: `POST
/v1/scripts/compile`(src/api/routers/scripts.py), BT-9 reproduction key, MP-1 publish.
"""

from __future__ import annotations

from src.core.script.artifact.compile import (
    SCRIPT_ERROR_CODES,
    CompiledScript,
    ScriptCompileError,
    compile_source,
    decl_positions,
)
from src.core.script.artifact.hash import HASH_ALGORITHM, HASH_SCHEMA, hash_payload, script_hash

__all__ = [
    "HASH_ALGORITHM",
    "HASH_SCHEMA",
    "SCRIPT_ERROR_CODES",
    "CompiledScript",
    "ScriptCompileError",
    "compile_source",
    "decl_positions",
    "hash_payload",
    "script_hash",
]
