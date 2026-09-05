"""AIOS Script 호환 패키지(§2.4/§9.4 DSL-10) — 기존 cond-v2 조건식을 DSL-1 AST로
변환하는 `cond_v2_bridge.py`. 신규 패키지, `src/core/**` SCAFFOLD zone. 순수
(I/O 없음). `src/core/strategy/**`(FROZEN_PAPER_ONLY)는 읽기 전용 — 수정하지 않는다.
소비자: DSL-11 `script_facade.py`, 아티팩트 `compat_map` 필드.
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
