"""ADR-2026-09-26-B SBX-1 -- `runtime/interpreter.py` runtime instruction-count budget.

`_Machine.run`'s dispatch loop (`for pos, instr in enumerate(ir.instrs)`) now counts every
instruction it dispatches and fail-closes with `ScriptRuntimeLimitError` once that count
exceeds `SCRIPT_RUNTIME_LIMIT`. This is a *dynamic* defense distinct from DSL-6's *static*
compile-time cap (`ScriptResourceLimitError`, `analysis/resources.py`): DSL-6 only bounds the
IR's own node count as produced by the normal parse/typecheck/lower pipeline, so it cannot
see an IR handed to `execute()` directly with more instructions than any DSL-6-compiled
program could ever contain -- e.g. an IR assembled by a future codegen/library-expansion path
whose size depends on a runtime input (DSL-6 has no visibility into that path at all). This
suite reproduces exactly that gap: it builds `IRProgram`s directly (bypassing the parser and
DSL-6) so the runtime counter is the only thing standing between an oversized IR and the
interpreter actually running it.

DoD: (1) concrete input with executed-instruction count == SCRIPT_RUNTIME_LIMIT + 1 raises
`ScriptRuntimeLimitError`, (2) a normal small script is unaffected (regression guard), (3) >=3
boundary tests (limit-1 passes / limit passes / limit+1 blocks -- the exact-limit choice is
fixed by this test), (4) one red-gate reproduction (raising the limit lets the same
over-budget IR complete silently), (5) one numeric throughput assertion for the added
per-instruction check.
"""

from __future__ import annotations

import pytest

from src.core.script.grammar.parser import parse
from src.core.script.ir import lower_program
from src.core.script.ir.ops import ConstInt, IRProgram, Neg, Store
from src.core.script.runtime.interpreter import (
    SCRIPT_RUNTIME_LIMIT,
    ScriptRuntimeLimitError,
    execute,
)
from src.core.script.runtime.series import Series
from tests.conftest import PerfBudget


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


# ---- (1) concrete reproduction with the real SCRIPT_RUNTIME_LIMIT constant ----


def test_default_runtime_limit_is_the_documented_million() -> None:
    assert SCRIPT_RUNTIME_LIMIT == 1_000_000


def test_ir_with_limit_plus_one_instrs_raises_script_runtime_limit_error() -> None:
    """DoD(1): concrete input whose executed-instruction count becomes
    SCRIPT_RUNTIME_LIMIT + 1, using the real default limit (no override)."""
    ir = _balanced_ir(SCRIPT_RUNTIME_LIMIT + 1)
    with pytest.raises(ScriptRuntimeLimitError) as exc_info:
        execute(ir, bar_count=1)
    assert str(SCRIPT_RUNTIME_LIMIT) in str(exc_info.value)


# ---- (3) boundary: limit-1 / limit / limit+1, on a small injected limit for speed ----
# (mirrors test_resources.py's `ResourceLimits(max_ops=3)` pattern for DSL-6 boundaries)


def test_runtime_limit_minus_one_instrs_passes() -> None:
    ir = _balanced_ir(4)
    result = execute(ir, bar_count=1, runtime_limit=5)
    assert isinstance(result.bindings["x"], int)


def test_runtime_limit_exact_instrs_passes() -> None:
    """Exact-limit choice fixed here: executed count == runtime_limit still passes
    (the loop only blocks once the count goes strictly above the budget)."""
    ir = _balanced_ir(5)
    result = execute(ir, bar_count=1, runtime_limit=5)
    assert isinstance(result.bindings["x"], int)


def test_runtime_limit_plus_one_instrs_blocks() -> None:
    ir = _balanced_ir(6)
    with pytest.raises(ScriptRuntimeLimitError):
        execute(ir, bar_count=1, runtime_limit=5)


# ---- (4) red-gate reproduction: raising the limit lets the same IR through silently ----


def test_removing_the_budget_lets_the_overbudget_ir_run_to_completion() -> None:
    """Same over-budget IR as `test_runtime_limit_plus_one_instrs_blocks`: with the
    guard's limit raised far above the IR's own instruction count (simulating the
    check being effectively disabled/misconfigured), it no longer fails -- proving the
    earlier raise came from this counter, not from `verify_stack` or some other
    unrelated check."""
    ir = _balanced_ir(6)
    with pytest.raises(ScriptRuntimeLimitError):
        execute(ir, bar_count=1, runtime_limit=5)
    result = execute(ir, bar_count=1, runtime_limit=10**9)
    assert isinstance(result.bindings["x"], int)


# ---- (2) regression: a normal, small script's behavior is unchanged ----


def test_normal_script_below_limit_is_unaffected() -> None:
    source = (
        "input close: series<float> = 0\n"
        "let doubled = close * 2\n"
        "signal go = doubled > close"
    )
    close = Series.of_floats([1.0, 2.0, 3.0])
    ir = lower_program(parse(source))

    result = execute(ir, bar_count=3, inputs={"close": close})

    assert result.bindings["doubled"] == Series((2.0, 4.0, 6.0))
    assert result.signals["go"] == Series((True, True, True))


# ---- (5) numeric throughput assertion for the added per-instruction check ----


@pytest.mark.perf
def test_budget_check_overhead_stays_within_backtest_budget_slice(
    perf_budget: PerfBudget,
) -> None:
    """ADR-2026-09-09-C Decision 1's backtest budget, sliced the same way as
    `test_interpreter.py::test_execution_latency_p95_within_backtest_budget_slice`: a
    30-`let` chain over one day of 1-minute bars (bar_count=1440) must still clear
    250ms p95 with the new per-instruction counter in the hot dispatch loop."""
    lines = [f"let v{i} = v{i - 1} * 1.0001 + 1 - 1" for i in range(1, 30)]
    source = "input close: series<float> = 0\nlet v0 = close\n" + "\n".join(lines)
    ir = lower_program(parse(source))
    bar_count = 1440
    close = Series.of_floats([float(i % 100) for i in range(bar_count)])

    samples = perf_budget.samples(
        lambda: execute(ir, bar_count=bar_count, inputs={"close": close}), n=20
    )
    cpu_values_ms = sorted(s.cpu_ms for s in samples)
    p95_ms = cpu_values_ms[min(int(len(cpu_values_ms) * 0.95), len(cpu_values_ms) - 1)]

    budget_ms = 250.0
    print(f"[SBX-1 runtime budget] p95={p95_ms:.3f}ms budget<{budget_ms:.0f}ms")
    assert p95_ms < budget_ms
