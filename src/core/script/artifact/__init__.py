"""AIOS Script 아티팩트 패키지(§9.4 DSL-12) — `hash.py`(script_hash)와
`compile.py`(DSL-2→7 파이프라인 조립 + §3.3 4종 오류·위치).

신규 패키지, `src/core/**` SCAFFOLD zone. 순수(I/O 없음). 소비자: `POST
/v1/scripts/compile`(src/api/routers/scripts.py), BT-9 재현 키, MP-1 게시.
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
