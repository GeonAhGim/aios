"""L4_script_interpreter_v1.0.md#SBX-1 -- runtime-limit boundary tests.

Split from `test_runtime_step_budget.py` (task-22089): isolates the
`test_runtime_limit_minus_one_instrs_passes` boundary test so it does not
share a file with the CPU-heavy `test_budget_check_overhead_stays_within_backtest_budget_slice`
perf test — parallel CI (`pytest_perf_parallel_2`) on the same file
causes wall-clock contention that makes perf budgets flaky.
"""

from __future__ import annotations

from src.core.script.ir.ops import ConstInt, IRProgram, Neg, Store
from src.core.script.runtime.interpreter import execute


def _balanced_ir(count: int) -> IRProgram:
    """A stack-balanced IR of exactly `count` instructions (`count >= 2`): one
    `const_int` push, `count - 2` `neg`(pop 1/push 1, keeps depth at 1) instructions,
    then one `store` that drains the stack back to 0 -- so `verify_stack` (called by
    `execute()` before the runtime-budget loop even starts) always accepts it,
    whatever `count` is."""
    assert count >= 2
    instrs: tuple[ConstInt | Neg | Store, ...] = (
        (ConstInt(value=1),) + (Neg(type="int"),) * (count - 2) + (Store(name="x", type="int"),)
    )
    return IRProgram(instrs=instrs)


# ---- (3) boundary: limit-1 passes, on a small injected limit for speed ----


def test_runtime_limit_minus_one_instrs_passes() -> None:
    """DoD(3): executed-instruction count == runtime_limit - 1 passes."""
    ir = _balanced_ir(4)
    result = execute(ir, bar_count=1, runtime_limit=5)
    assert isinstance(result.bindings["x"], int)
