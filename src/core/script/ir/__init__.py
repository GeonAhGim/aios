"""AIOS Script IR package (§9.4 DSL-7) — ops set (`ops.py`) and AST-to-IR lowering (`lower.py`).

New package under `src/core/**` SCAFFOLD zone. Pure (no I/O). Consumers: DSL-8
interpreter (execution), DSL-12 `artifact/hash.py` (includes IR bytes in script_hash).
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
    Request,
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
    "Request",
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
