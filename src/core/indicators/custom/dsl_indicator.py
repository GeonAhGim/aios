"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9 IND-8 —
Turns a compiled AIOS Script into a SCRIPT-tier indicator catalog entry.

Prerequisites: DSL-9 (task-1554/2140, `ta.*` builtins) and IND-12
(task-1730, `catalog/registry_tiers.py` three-tier registry).

This module does not compile scripts or hash them itself — it only calls
`artifact/compile.py::compile_source`, which in turn calls
`artifact/hash.py::script_hash` internally (`CompiledScript.script_hash`).
No hash scheme or compile stage is reimplemented here (duplicate-context
prohibited, see `registry_tiers.py` module docstring for the same rule).

Catalog exposure is limited to *implementing* `registry_tiers.py`'s
`ScriptIndicatorEntry`/`ScriptIndicatorSource` protocols — `registry_tiers.py`,
`registry.py`, and `engine/` are not touched (task-2141 owns those files).

Storage is in this leaf's scope only as an in-memory port
(`InMemoryScriptIndicatorSource`): no table, no migration, no HTTP router.
A real persistence adapter is a later leaf and can implement the same
`ScriptIndicatorSource` protocol without changing any caller of this module.

Deriving `IndicatorSpec` (L01) from a `CompiledScript`:
- A `series<float>`/`series<bool>` `input` decl names a market-data column
  the script reads (e.g. `input close: series<float> = 0`, DSL-10's
  `cond_v2_bridge.py` module docstring shows the same shape) -> it becomes
  one `IndicatorSpec.inputs` entry, in declared order.
- An `int` `input` decl is a script-baked scalar parameter -> it becomes one
  `ParamSpec` with `min == max == default` (the script hard-codes exactly one
  value at compile time; DSL-1 has no syntax for a *range*, so there is no
  tunable span to expose — `min`/`max` collapsing to the single compiled
  value is the correct, not merely convenient, representation).
- `float`/`bool` scalar `input` decls have no representation in `ParamSpec`
  (L01 only models integer-ranged params) -> rejected fail-closed
  (`DslIndicatorError`), not silently coerced.
- Each `plot(...)` in the script becomes one positional output
  (`plot_0`, `plot_1`, ...) with a generic line/own-scale/separate-pane
  `PlotSpec` -- the `Plot` IR node carries no display metadata beyond
  `style`, whose semantics are undefined by DSL-1 decision (`ir/ops.py`
  module docstring) and therefore cannot be interpreted here. A script with
  zero `plot(...)` decls produces nothing to chart and is rejected
  fail-closed rather than registered as an empty indicator.
- `lookback` is the compiler's own conservative static estimate
  (`ResourceEstimate.lookback_total`, DSL-6) as a constant callable — a
  compiled script has already baked its periods into IR constants, so unlike
  a TA-Lib `IndicatorSpec.lookback`, the incoming `params` dict cannot change
  a script indicator's lookback after the fact.
"""
from __future__ import annotations

import threading
from collections.abc import Sequence
from uuid import UUID

from src.core.indicators.catalog.registry_tiers import ScriptIndicatorEntry
from src.core.indicators.spec import IndicatorSpec, ParamSpec, PlotSpec
from src.core.script.analysis.resources import DEFAULT_LIMITS, ResourceLimits
from src.core.script.artifact.compile import CompiledScript, compile_source
from src.core.script.ir.ops import DeclareInput, Plot

__all__ = [
    "DslIndicatorError",
    "InMemoryScriptIndicatorSource",
    "compile_script_indicator",
]

_SERIES_INPUT_TYPES = frozenset({"series<float>", "series<bool>"})


class DslIndicatorError(Exception):
    """Raised when a script compiles but cannot be represented as an `IndicatorSpec`."""


def _param_spec(instr: DeclareInput) -> ParamSpec:
    value = instr.value
    if not isinstance(value, int) or isinstance(value, bool):
        raise DslIndicatorError(
            f"{instr.name!r}: 'int' input must carry an int literal, got {value!r}"
        )
    return ParamSpec(name=instr.name, min=value, max=value, default=value)


def _script_indicator_spec(name: str, compiled: CompiledScript) -> IndicatorSpec:
    inputs: list[str] = []
    params: list[ParamSpec] = []
    for instr in compiled.ir.instrs:
        if not isinstance(instr, DeclareInput):
            continue
        if instr.type in _SERIES_INPUT_TYPES:
            inputs.append(instr.name)
        elif instr.type == "int":
            params.append(_param_spec(instr))
        else:
            raise DslIndicatorError(
                f"{name!r}: scalar input {instr.name!r} has type {instr.type!r}, "
                "which ParamSpec (L01) cannot represent (only 'int' scalar inputs are)"
            )

    plot_count = sum(1 for instr in compiled.ir.instrs if isinstance(instr, Plot))
    if plot_count == 0:
        raise DslIndicatorError(f"{name!r}: script has no plot(...) output to register")

    outputs = tuple(f"plot_{i}" for i in range(plot_count))
    plots = tuple(
        PlotSpec(kind="line", scale="own", default_pane="separate") for _ in range(plot_count)
    )
    lookback_total = compiled.estimate.lookback_total

    def _constant_lookback(_params: dict[str, int]) -> int:
        return lookback_total

    return IndicatorSpec(
        name=name,
        inputs=tuple(inputs),
        params=tuple(params),
        outputs=outputs,
        lookback=_constant_lookback,
        plots=plots,
    )


def compile_script_indicator(
    name: str,
    source: str,
    *,
    tenant_id: UUID,
    registry_version: str,
    limits: ResourceLimits = DEFAULT_LIMITS,
) -> ScriptIndicatorEntry:
    """Compile `source` and wrap it as one tenant-scoped `ScriptIndicatorEntry`.

    `ScriptCompileError` (4-kind §3.3 taxonomy) propagates unwrapped — this
    function does not re-taxonomize compile failures. `DslIndicatorError`
    is raised only once compilation itself already succeeded but the result
    still cannot become an `IndicatorSpec` (see module docstring).
    """
    compiled = compile_source(source, registry_version=registry_version, limits=limits)
    spec = _script_indicator_spec(name, compiled)
    return ScriptIndicatorEntry(
        name=name,
        tenant_id=tenant_id,
        spec=spec,
        script_hash=compiled.script_hash,
    )


class InMemoryScriptIndicatorSource:
    """In-memory `ScriptIndicatorSource` (registry_tiers.py Protocol) — no I/O.

    Keyed by `(tenant_id, name)`; `register` replaces any prior entry under
    the same key (re-registering a name is a version bump, tracked via the
    new entry's `script_hash` — the catalog layer already exposes
    `script_hash` as `CatalogEntry.version`, see `registry_tiers.py`).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_tenant: dict[UUID, dict[str, ScriptIndicatorEntry]] = {}

    def register(self, entry: ScriptIndicatorEntry) -> None:
        with self._lock:
            self._by_tenant.setdefault(entry.tenant_id, {})[entry.name] = entry

    def list_for_tenant(self, tenant_id: UUID) -> Sequence[ScriptIndicatorEntry]:
        with self._lock:
            return tuple(self._by_tenant.get(tenant_id, {}).values())
