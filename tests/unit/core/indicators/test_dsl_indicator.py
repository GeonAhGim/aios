"""IND-8 — `indicators/custom/dsl_indicator.py` 계약 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9 IND-8.
DoD: compiled AIOS Script -> SCRIPT-tier `ScriptIndicatorEntry` (compile/hash
call-only, no reimplementation), in-memory `ScriptIndicatorSource` port with
tenant isolation. D2 floor (ADR-2026-09-09-C Decision 1): negative tests >= 3,
failure-injection 1, numeric perf assertion 1, gate-red repro 1.
"""

from __future__ import annotations

import time
import uuid

import pytest

from scripts.check_import_linter import ROOT as LINTER_ROOT
from scripts.check_import_linter import _eval_forbidden, _imports_of, parse_contracts
from src.core.indicators.custom.dsl_indicator import (
    DslIndicatorError,
    InMemoryScriptIndicatorSource,
    _script_indicator_spec,
    compile_script_indicator,
)
from src.core.script.analysis.resources import ResourceEstimate
from src.core.script.artifact.compile import CompiledScript, ScriptCompileError, compile_source
from src.core.script.artifact.hash import script_hash
from src.core.script.grammar.ast import GRAMMAR_VERSION
from src.core.script.ir.ops import IR_VERSION, DeclareInput, IRProgram, Plot

REG = "r" * 64

VALID_SOURCE = (
    "input length: int = 14\n"
    "input close: series<float> = 0\n"
    "let rsi_val = ta.rsi(close, length)\n"
    "plot(rsi_val)\n"
)

NO_PLOT_SOURCE = (
    "input length: int = 14\n"
    "input close: series<float> = 0\n"
    "let rsi_val = ta.rsi(close, length)\n"
)

FLOAT_INPUT_SOURCE = (
    "input rate: float = 1.5\n"
    "input close: series<float> = 0\n"
    "let scaled = close * rate\n"
    "plot(scaled)\n"
)

BOOL_INPUT_SOURCE = (
    # Grammar only allows a NUMBER literal after `=` (parser.py `_input_decl`),
    # even for a `bool`-typed input -- `1` is the only way to reach this decl.
    "input flag: bool = 1\n"
    "input close: series<float> = 0\n"
    "plot(close)\n"
)

SYNTAX_ERROR_SOURCE = "plot(\n"


# ---- 성공 경로: 계약 그대로 -------------------------------------------------


def test_compile_script_indicator_derives_inputs_params_outputs_and_reuses_hash() -> None:
    tenant_id = uuid.uuid4()
    entry = compile_script_indicator(
        "my_rsi", VALID_SOURCE, tenant_id=tenant_id, registry_version=REG
    )
    assert entry.name == "my_rsi"
    assert entry.tenant_id == tenant_id
    assert entry.spec.inputs == ("close",)
    assert [p.name for p in entry.spec.params] == ["length"]
    assert (entry.spec.params[0].min, entry.spec.params[0].max, entry.spec.params[0].default) == (
        14,
        14,
        14,
    )
    assert entry.spec.outputs == ("plot_0",)
    assert len(entry.spec.plots) == 1

    compiled = compile_source(VALID_SOURCE, registry_version=REG)
    assert entry.script_hash == compiled.script_hash
    assert entry.script_hash == script_hash(
        source=VALID_SOURCE, ir=compiled.ir, registry_version=REG
    )


def test_lookback_is_constant_from_resource_estimate_ignoring_params_arg() -> None:
    entry = compile_script_indicator(
        "my_rsi2", VALID_SOURCE, tenant_id=uuid.uuid4(), registry_version=REG
    )
    compiled = compile_source(VALID_SOURCE, registry_version=REG)
    assert entry.spec.lookback({}) == compiled.estimate.lookback_total
    assert entry.spec.lookback({"length": 999}) == compiled.estimate.lookback_total


# ---- D2 명시 거부(pytest.raises) x3 -----------------------------------------


def test_rejects_script_with_no_plot_output() -> None:
    with pytest.raises(DslIndicatorError, match="no plot"):
        compile_script_indicator(
            "no_plot", NO_PLOT_SOURCE, tenant_id=uuid.uuid4(), registry_version=REG
        )


def test_rejects_float_scalar_input() -> None:
    with pytest.raises(DslIndicatorError, match="float"):
        compile_script_indicator(
            "float_in", FLOAT_INPUT_SOURCE, tenant_id=uuid.uuid4(), registry_version=REG
        )


def test_rejects_bool_scalar_input() -> None:
    with pytest.raises(DslIndicatorError, match="bool"):
        compile_script_indicator(
            "bool_in", BOOL_INPUT_SOURCE, tenant_id=uuid.uuid4(), registry_version=REG
        )


def test_script_compile_error_propagates_unwrapped_not_taxonomized_as_dsl_indicator_error() -> (
    None
):
    """Compile failures stay in the §3.3 4-kind taxonomy — this leaf must not
    swallow a `ScriptCompileError` into its own `DslIndicatorError`, which
    would hide the real (line, col) diagnostic from the DSL editor."""
    with pytest.raises(ScriptCompileError):
        compile_script_indicator(
            "bad_syntax", SYNTAX_ERROR_SOURCE, tenant_id=uuid.uuid4(), registry_version=REG
        )


# ---- D2 실패 주입 ------------------------------------------------------------


def test_param_spec_rejects_bool_masquerading_as_int_declare_input() -> None:
    """Python's `bool` is a subclass of `int`, so if a future DSL-4/DSL-7
    regression let a `bool` literal through as a `DeclareInput` typed `"int"`
    (type checker/lowering drift, not something this leaf's compiler call can
    prevent by construction), a naive `isinstance(value, int)` check would
    silently accept it as a valid integer param. Injecting that exact
    malformed IR node directly (bypassing the real compiler, which would
    never emit this today) proves `_script_indicator_spec` still rejects it
    fail-closed instead of registering a bogus `ParamSpec(min=True, ...)`."""
    bad_ir = IRProgram(
        ir_version=IR_VERSION,
        grammar_version=GRAMMAR_VERSION,
        instrs=(
            DeclareInput(name="flag", type="int", value=True),
            Plot(type="series<float>"),
        ),
    )
    compiled = CompiledScript(
        source="synthetic",
        ir=bad_ir,
        ir_bytes=b"{}",
        estimate=ResourceEstimate(plot_count=1),
        registry_version=REG,
        script_hash="0" * 64,
    )
    with pytest.raises(DslIndicatorError, match="int.*literal"):
        _script_indicator_spec("bad", compiled)


# ---- 테넌트 격리 (in-memory 포트) --------------------------------------------


def test_in_memory_source_isolates_entries_by_tenant() -> None:
    source = InMemoryScriptIndicatorSource()
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    entry_a = compile_script_indicator(
        "shared_name", VALID_SOURCE, tenant_id=tenant_a, registry_version=REG
    )
    source.register(entry_a)
    assert [e.name for e in source.list_for_tenant(tenant_a)] == ["shared_name"]
    assert source.list_for_tenant(tenant_b) == ()


def test_in_memory_source_register_replaces_prior_entry_same_key() -> None:
    source = InMemoryScriptIndicatorSource()
    tenant_id = uuid.uuid4()
    entry_v1 = compile_script_indicator(
        "my_rsi", VALID_SOURCE, tenant_id=tenant_id, registry_version=REG
    )
    source.register(entry_v1)
    other_source = (
        "input length: int = 21\n"
        "input close: series<float> = 0\n"
        "let rsi_val = ta.rsi(close, length)\n"
        "plot(rsi_val)\n"
    )
    entry_v2 = compile_script_indicator(
        "my_rsi", other_source, tenant_id=tenant_id, registry_version=REG
    )
    source.register(entry_v2)
    listed = source.list_for_tenant(tenant_id)
    assert len(listed) == 1
    assert listed[0].script_hash == entry_v2.script_hash
    assert listed[0].script_hash != entry_v1.script_hash


# ---- D2 수치 성능 단언 --------------------------------------------------------


def test_compile_script_indicator_conversion_overhead_stays_within_budget() -> None:
    """§9.4 DSL-12 already budgets raw `compile_source` at <=300ms
    (ADR-2026-09-09-C Decision 1, `test_compile.py`). This pins the marginal
    cost this leaf adds on top (IR walk -> `IndicatorSpec`) so it cannot
    quietly regress into a per-indicator O(n^2) scan without a test noticing."""
    tenant_id = uuid.uuid4()
    repeats = 30
    start = time.perf_counter()
    for i in range(repeats):
        compile_script_indicator(
            f"perf_{i}", VALID_SOURCE, tenant_id=tenant_id, registry_version=REG
        )
    elapsed_ms = (time.perf_counter() - start) * 1000
    per_call_ms = elapsed_ms / repeats
    budget_ms = 300.0  # matches DSL-12's own compile_source budget (this leaf adds a thin pass)
    assert per_call_ms < budget_ms, (
        f"compile_script_indicator took {per_call_ms:.2f}ms/call over {repeats} reps, "
        f"budget {budget_ms}ms"
    )


# ---- D2 게이트 적색 재현 ------------------------------------------------------


def test_import_linter_core_no_io_catches_dsl_indicator_foundation_import_regression() -> None:
    """This module's own docstring states storage stays an in-memory port with
    "no table, no migration, no HTTP router" — i.e. it must stay inside
    `core-no-io`'s `src.core` boundary (`.importlinter`, no `src.foundation`/
    `src.api`/`src.exchanges` imports). Proves the real evaluator
    (`scripts/check_import_linter.py`) fires red for a synthetic regression
    shape (this module reaching into foundation persistence directly) and
    stays green for its real current imports."""
    contracts = parse_contracts(LINTER_ROOT / ".importlinter")
    core_no_io = next(c for c in contracts if c["id"] == "core-no-io")

    module = "src.core.indicators.custom.dsl_indicator"
    regressed_graph = {module: {"src.foundation.marketplace.persistence"}}
    hits = _eval_forbidden(regressed_graph, core_no_io)
    assert len(hits) == 1
    assert hits[0][0] == module

    real_imports = _imports_of(
        LINTER_ROOT / "src/core/indicators/custom/dsl_indicator.py", module, is_package=False
    )
    assert _eval_forbidden({module: real_imports}, core_no_io) == []
