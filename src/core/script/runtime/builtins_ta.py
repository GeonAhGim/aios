"""L4_analytics_authoring_backtest_marketplace_v1.0.md §3.3/§9.4 DSL-9(a) —
AIOS Script `ta.*` built-in functions: indicator registry (L02) version pinning +
IND-1 incremental engine delegation.

Pure module (no I/O). This file contains no indicator computation (I-04 single-source):
spec lookup, parameter validation, and lookback are handled by
`src/core/indicators/registry.py` (L02); computation is delegated as-is to
`engine/incremental.py` (IND-1 `IncrementalIndicator`). Tests prove engine calls
via spy (I-10).

Registry version pinning (§3.3 "`ta.*` pins to IND registry version"):
- `TaBuiltins.registry_version` = `IndicatorRegistry.registry_hash()` (spec canonical
  hash, L02 88fbbbf). Matches the same string used as input for DSL-12 `artifact/hash.py`
  `registry_version`, ensuring "same script_hash = same registry computation" holds.
- If the compiled artifact passes a recorded version as `expected_registry_version`,
  the registry rejects table creation (rather than silently computing under a different
  registry) when it differs (`INDICATOR_REGISTRY_MISMATCH`, fail-closed).
- Each call records a `TaCall` (indicator, output, validated params, lookback,
  registry_hash) in the `calls` ledger. `Series` is an immutable type holding only
  values, so metadata is recorded in the ledger rather than attached to values
  (backtest reports, audit evidence inputs).

Call convention `ta.<ident>(<inputs...>, <params...>)`:
- ident is derived from the registry: single-output indicators use the lowercase name
  (`ta.sma`); multi-output indicators expose all `<name>_<output>` keys
  (`ta.macd_signal`, `ta.bbands_upperband`, `ta.stoch_slowd`) plus the lowercase alias
  for the first output (`ta.macd` = macd line).
- The first `len(spec.inputs)` arguments are input series (in spec.inputs order:
  `ta.atr(high, low, close, 14)`). When a scalar arrives, it is broadcast to bar count
  (same rule as DSL-8 `broadcast`: static type may be float but the runtime value is a
  series, which is valid).
- Subsequent arguments are parameters (in spec.params order). Defaults from the registry
  apply when omitted. Each value must be a scalar int (bool, float, and series are
  rejected); the registry validates the range.
- na: skip the *leading* na region in inputs (prefix created by `[n]` shift) and feed
  from the first complete bar onward (output in that region is na). Internal na after the
  prefix raises `INDICATOR_INPUT_INVALID` (zero substitution forbidden). If the number of
  bars fed to the engine is ≤ lookback and no values can be produced, raise
  `INDICATOR_LOOKBACK_INSUFFICIENT` (do not fall back to an all-na series).

All errors are `BuiltinCallError` (subclass of `ScriptRuntimeError`, `reason`=code).
`IndicatorError.code` from the registry/engine is passed through as `reason` unchanged.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.core.indicators.engine.incremental import IncrementalIndicator
from src.core.indicators.registry import DEFAULT_REGISTRY, IndicatorError, IndicatorRegistry
from src.core.indicators.spec import IndicatorSpec
from src.core.script.runtime.builtins_math import MATH_BUILTINS, BuiltinCallError
from src.core.script.runtime.series import Scalar, ScriptRuntimeError, Series, Value, broadcast

if TYPE_CHECKING:
    from src.core.script.runtime.interpreter_types import Builtin, CallSite

__all__ = ["TaBuiltins", "TaCall", "default_builtins", "ta_idents"]

_NS = "ta"


@dataclass(frozen=True)
class TaCall:
    """Ledger entry for one `ta.*` call (evidence of registry version pinning)."""

    ident: str
    indicator: str
    output: str
    params: tuple[tuple[str, int], ...]
    lookback: int
    registry_hash: str
    bar_count: int
    fed_from: int
    """Index of the first bar fed to the engine (leading na prefix length)."""


def ta_idents(spec: IndicatorSpec) -> dict[str, str]:
    """`ident → output` table exposed by one spec (module docstring convention)."""
    lowered = spec.name.lower()
    if spec.outputs == ("value",):
        return {lowered: "value"}
    table = {f"{lowered}_{output}": output for output in spec.outputs}
    table[lowered] = spec.outputs[0]
    return table


class TaBuiltins:
    """Fixed `ta.*` builtin table on one registry + call ledger."""

    def __init__(
        self,
        registry: IndicatorRegistry = DEFAULT_REGISTRY,
        *,
        expected_registry_version: str | None = None,
    ) -> None:
        self._registry = registry
        self.registry_version = registry.registry_hash()
        if (
            expected_registry_version is not None
            and expected_registry_version != self.registry_version
        ):
            raise BuiltinCallError(
                "INDICATOR_REGISTRY_MISMATCH",
                f"컴파일 산출물 레지스트리 {expected_registry_version[:12]}… != "
                f"현재 {self.registry_version[:12]}…",
            )
        self._calls: list[TaCall] = []
        self.table: dict[tuple[str, str], Builtin] = {}
        for name in sorted(registry_names(registry)):
            for ident, output in ta_idents(registry.get(name)).items():
                self.table[(_NS, ident)] = self._bind(ident, name, output)

    @property
    def calls(self) -> tuple[TaCall, ...]:
        return tuple(self._calls)

    def _bind(self, ident: str, name: str, output: str) -> Builtin:
        def builtin(args: tuple[Value, ...], site: CallSite) -> Value:
            return self._call(ident, name, output, args, site.bar_count)

        return builtin

    # ---- calls ----

    def _call(
        self, ident: str, name: str, output: str, args: tuple[Value, ...], bar_count: int
    ) -> Series:
        where = f"ta.{ident}()"
        spec = self._registry.get(name)
        n_in, n_params = len(spec.inputs), len(spec.params)
        if not n_in <= len(args) <= n_in + n_params:
            raise BuiltinCallError(
                "SCRIPT_BUILTIN_ARITY",
                f"{where} 인자 {n_in}~{n_in + n_params}개 필요(받음 {len(args)})",
            )
        columns = _input_columns(where, spec, args[:n_in], bar_count)
        params = _param_values(where, spec, args[n_in:])
        try:
            engine = IncrementalIndicator(name, params, self._registry)
        except IndicatorError as exc:
            raise BuiltinCallError(exc.code, f"{where} {exc.code}") from exc
        fed_from = _leading_na(columns, bar_count)
        if bar_count - fed_from <= engine.lookback:
            raise BuiltinCallError(
                "INDICATOR_LOOKBACK_INSUFFICIENT",
                f"{where} lookback {engine.lookback} >= 유효 봉 수 {bar_count - fed_from}",
            )
        out: list[Scalar] = [None] * fed_from
        for t in range(fed_from, bar_count):
            bar: dict[str, float] = {}
            for key, col in columns.items():
                v = col[t]
                if v is None:
                    raise BuiltinCallError(
                        "INDICATOR_INPUT_INVALID", f"{where} {key} 봉 #{t}: 선행 접두 이후의 na"
                    )
                bar[key] = v
            try:
                out.append(engine.update(bar)[output])
            except IndicatorError as exc:
                raise BuiltinCallError(exc.code, f"{where} 봉 #{t}: {exc.code}") from exc
        self._calls.append(
            TaCall(
                ident=ident,
                indicator=name,
                output=output,
                params=tuple(sorted(engine.params.items())),
                lookback=engine.lookback,
                registry_hash=self.registry_version,
                bar_count=bar_count,
                fed_from=fed_from,
            )
        )
        return Series.of_floats(out)


def default_builtins(
    registry: IndicatorRegistry = DEFAULT_REGISTRY,
    *,
    registry_version: str | None = None,
) -> dict[tuple[str, str], Builtin]:
    """Register DSL-9a builtin table: `math.*` (MATH_BUILTINS) + `ta.*` (fixed registry).

    Pass `registry_version` (registry hash recorded by the compiled artifact) to make
    `TaBuiltins` reject when it differs from the current
    registry. Create `TaBuiltins` directly and merge `.table` if the call ledger is
    needed. `interpreter.py` does not import the indicator registry (DSL-8 purity static
    check), so this entry point lives here.
    """
    ta = TaBuiltins(registry, expected_registry_version=registry_version)
    return {**MATH_BUILTINS, **ta.table}


def registry_names(registry: IndicatorRegistry) -> tuple[str, ...]:
    """Indicator names registered in the registry. Reads spec dict since
    L02 lacks an enumeration API."""
    specs: Mapping[str, IndicatorSpec] = registry._specs  # L02 lacks enumeration API (read-only)
    return tuple(specs)


def _input_columns(
    where: str, spec: IndicatorSpec, args: tuple[Value, ...], bar_count: int
) -> dict[str, tuple[float | None, ...]]:
    columns: dict[str, tuple[float | None, ...]] = {}
    for key, arg in zip(spec.inputs, args, strict=True):
        try:
            raw = broadcast(arg, bar_count).values
        except ScriptRuntimeError as exc:
            raise BuiltinCallError("INDICATOR_INPUT_INVALID", f"{where} {key}: {exc}") from exc
        column: list[float | None] = []
        for t, v in enumerate(raw):
            if v is None:
                column.append(None)
            elif isinstance(v, bool) or not isinstance(v, int | float):
                raise BuiltinCallError(
                    "INDICATOR_INPUT_INVALID", f"{where} {key} 봉 #{t}: 수치가 아닙니다: {v!r}"
                )
            else:
                column.append(float(v))
        columns[key] = tuple(column)
    return columns


def _param_values(where: str, spec: IndicatorSpec, args: tuple[Value, ...]) -> dict[str, int]:
    params: dict[str, int] = {}
    for param_spec, arg in zip(spec.params, args, strict=False):
        if isinstance(arg, bool) or not isinstance(arg, int):
            raise BuiltinCallError(
                "STRATEGY_PARAM_OUT_OF_RANGE",
                f"{where} {param_spec.name}: 스칼라 int여야 합니다: {arg!r}",
            )
        params[param_spec.name] = arg
    return params


def _leading_na(columns: Mapping[str, tuple[float | None, ...]], bar_count: int) -> int:
    """Index of the first bar where all inputs are non-na (returns bar_count if none)."""
    for t in range(bar_count):
        if all(col[t] is not None for col in columns.values()):
            return t
    return bar_count

