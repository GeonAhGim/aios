"""AIOS Script IR 패키지(§9.4 DSL-7) — 명령 집합(`ops.py`)과 AST→IR 로우어링(`lower.py`).

신규 패키지, `src/core/**` SCAFFOLD zone. 순수(I/O 없음). 소비자: DSL-8
인터프리터(실행), DSL-12 `artifact/hash.py`(script_hash에 IR 바이트 포함).
"""
from __future__ import annotations

from src.core.script.ir.lower import ScriptLowerError, lower_expr, lower_program
from src.core.script.ir.ops import (
    IR_VERSION,
    BinOp,
    Call,
    ConstFloat,
    ConstInt,
    DeclareInput,
    Index,
    Instr,
    IRNode,
    IRProgram,
    IRStackError,
    Load,
    Neg,
    Not,
    Order,
    Plot,
    Signal,
    Store,
    from_bytes,
    stack_effect,
    to_bytes,
    verify_stack,
)

__all__ = [
    "IR_VERSION",
    "BinOp",
    "Call",
    "ConstFloat",
    "ConstInt",
    "DeclareInput",
    "IRNode",
    "IRProgram",
    "IRStackError",
    "Index",
    "Instr",
    "Load",
    "Neg",
    "Not",
    "Order",
    "Plot",
    "ScriptLowerError",
    "Signal",
    "Store",
    "from_bytes",
    "lower_expr",
    "lower_program",
    "stack_effect",
    "to_bytes",
    "verify_stack",
]
