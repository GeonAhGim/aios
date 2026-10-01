"""L4_analytics_authoring_backtest_marketplace_v1.0.md §2.4/§3.3/§9.4 DSL-10 —
cond-v2 expression → AIOS Script (DSL-1 AST) conversion bridge + `compat_map`.

Input is a cond-v2 v1 flat string interpreted by
`src/core/strategy/condition_evaluator.py` (FROZEN_PAPER_ONLY, read-only) —
a form combining `"{KEY} {OP} {NUMBER}"` tokens produced by `ConditionCompiler`
with only `" AND "` or `" OR "`. The L4_strategy §3.1
`condition_ast.py`/`condition_parser.py` (parentheses, NOT, `@tf`) are not
yet in the repository; this bridge accepts only the grammar the current
evaluator actually consumes and rejects anything else along with its position
(character offset, part index) (partial conversion prohibited). The output is
a `Program` obtained by feeding the generated source text through DSL-3
`parse()`, so conformance to §3.3 grammar is guaranteed by construction
(no new grammar).

Conversion rules (following §3.3 "cond-v2 compat" example verbatim):
    RSI_timeperiod14 < 30 AND SMA_timeperiod20 > 100
  → input close: series<float> = 0
    let RSI_timeperiod14 = ta.rsi(close, 14)
    let SMA_timeperiod20 = ta.sma(close, 20)
    signal cond = RSI_timeperiod14 < 30 and SMA_timeperiod20 > 100
Indicator names, parameters, and input series are validated against
`indicators.registry.DEFAULT_REGISTRY` (same registry used by the cond-v2
execution loop in `market_state.py`) and defaults are filled in. The argument
order of `ta.*` calls follows the registry `params` declaration order —
unverified: once DSL-9 `builtins_ta.py` is finalized, only `_TA_IDENT` needs
updating. Multi-output indicators (MACD, BBANDS, STOCH) implicitly select the
primary output in cond-v2, but this bridge rejects them because the DSL call's
return output type is undecided (no signal fabrication by guess).

Semantic differences (both within "non-fired" scope, tests assert explicitly):
- cond-v2 uses left-to-right short-circuit evaluation, deferring judgment at
  the first missing key (raises exception); DSL uses Kleene three-valued logic
  (`na and False = False`). Firing (True) always matches.
- For cross operations, cond-v2 returns False when no previous tick exists;
  DSL returns na (bar 0).

`compat_map` is a pure dict carried in the compile artifact (no DB storage or
migration): maps original node ids (`root`, `atom:i`) → script lines/identifiers,
the original expression's `node_hash` (= sha256 of canonical string per
L4_strategy §3.1 `to_canonical` rules: whitespace normalization, leaf order
preservation), and the conversion result `script_hash` (`compile_cond_v2`).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from src.core.indicators.registry import DEFAULT_REGISTRY, IndicatorError, IndicatorRegistry
from src.core.script.artifact.compile import CompiledScript, compile_source
from src.core.script.grammar.ast import GRAMMAR_VERSION, Program
from src.core.script.grammar.parser import parse

COMPAT_SCHEMA: Final = "cond-v2-compat-1"
SOURCE_GRAMMAR_VERSION: Final = "cond-v2"
SIGNAL_NAME: Final = "cond"

# Same grammar as condition_evaluator._ATOMIC_RE / market_state._KEY_RE (tests assert synchronization).
_ATOMIC_RE: Final = re.compile(
    r"^(?P<key>\S+)\s+(?P<op>>=|<=|==|>|<|CROSSES_ABOVE|CROSSES_BELOW)\s+"
    r"(?P<threshold>-?\d+(?:\.\d+)?)$"
)
_KEY_RE: Final = re.compile(r"^(?P<indicator>[A-Z]+)(?P<params>(?:_[a-z]+\d+)*)$")
_PARAM_RE: Final = re.compile(r"_([a-z]+)(\d+)")
_OP_MAP: Final[Mapping[str, str]] = {
    ">": ">", "<": "<", ">=": ">=", "<=": "<=", "==": "==",
    "CROSSES_ABOVE": "crosses_above", "CROSSES_BELOW": "crosses_below",
}  # fmt: skip
_JOINERS: Final[Mapping[str, str]] = {" AND ": "and", " OR ": "or"}
_INPUT_ORDER: Final = ("open", "high", "low", "close", "volume")
# Registry name → DSL `ta.` identifier (unverified: once DSL-9 finalizes, only this table needs updating).
_TA_IDENT: Final[Mapping[str, str]] = {
    n: n.lower() for n in ("SMA", "EMA", "RSI", "ATR", "CCI", "WILLR", "MFI", "OBV")
}


class CondV2BridgeError(Exception):
    """Conversion rejection (fail-closed). `offset` is the 0-based character
    position in the original cond-v2 string, `part_index` is the combined part
    index. `code` is the cause classification (reuses cond-v2/registry codes)."""

    def __init__(self, code: str, message: str, *, offset: int, part_index: int) -> None:
        super().__init__(f"[{code}] {message} (offset {offset}, part {part_index})")
        self.code = code
        self.message = message
        self.offset = offset
        self.part_index = part_index


@dataclass(frozen=True)
class _Atom:
    index: int
    offset: int
    key: str
    op: str
    threshold: str
    indicator: str
    params: tuple[int, ...]
    inputs: tuple[str, ...]


@dataclass(frozen=True)
class BridgedScript:
    """`source` is the DSL source text, `program == parse(source)`,
    `compat_map` is a JSON-compatible dict."""

    expression: str
    source: str
    program: Program
    compat_map: dict[str, Any]


@dataclass(frozen=True)
class CompiledCondV2:
    compiled: CompiledScript
    compat_map: dict[str, Any]


def canonical_expression(expression: str) -> str:
    """L4_strategy §3.1 `to_canonical` (flat form): whitespace normalization,
    leaf order preservation."""
    joiner, parts = _split(expression)
    canon = [" ".join(p.strip().split()) for p in parts]
    return joiner.join(canon) if joiner else canon[0]


def node_hash(expression: str) -> str:
    """`sha256(to_canonical(expr))` — `node_hash` definition from L4_strategy
    §3.0 table."""
    return hashlib.sha256(canonical_expression(expression).encode("utf-8")).hexdigest()


def bridge_cond_v2(
    expression: str, *, registry: IndicatorRegistry = DEFAULT_REGISTRY
) -> BridgedScript:
    """Converts a cond-v2 string to `BridgedScript`. Unknown operators,
    unsupported indicators/parameters, mixed joiners, and multi-output
    indicators are fully rejected via `CondV2BridgeError` (with position info)."""
    joiner, parts = _split(expression)
    atoms: list[_Atom] = []
    pos = 0
    for i, part in enumerate(parts):
        offset = pos + (len(part) - len(part.lstrip()))
        atoms.append(_parse_atom(part.strip(), i, offset, registry))
        pos += len(part) + len(joiner)
    lines: list[str] = []
    inputs = sorted({name for a in atoms for name in a.inputs}, key=_INPUT_ORDER.index)
    lines.extend(f"input {name}: series<float> = 0" for name in inputs)
    let_line: dict[str, int] = {}
    for a in atoms:
        if a.key in let_line:
            continue
        args = ", ".join([*a.inputs, *map(str, a.params)])
        lines.append(f"let {a.key} = ta.{_TA_IDENT[a.indicator]}({args})")
        let_line[a.key] = len(lines)
    op_word = _JOINERS[joiner] if joiner else ""
    body = f" {op_word} ".join(f"{a.key} {_OP_MAP[a.op]} {a.threshold}" for a in atoms)
    lines.append(f"signal {SIGNAL_NAME} = {body}")
    source = "\n".join(lines)
    nodes: dict[str, Any] = {
        "root": {"kind": "root", "op": op_word or None, "line": len(lines), "ident": SIGNAL_NAME}
    }
    for a in atoms:
        nodes[f"atom:{a.index}"] = {
            "kind": "atom", "key": a.key, "op": a.op, "threshold": a.threshold,
            "offset": a.offset, "line": let_line[a.key], "ident": a.key,
            "call": {"ns": "ta", "ident": _TA_IDENT[a.indicator], "args": list(a.params)},
        }  # fmt: skip
    compat_map: dict[str, Any] = {
        "schema": COMPAT_SCHEMA,
        "source_grammar_version": SOURCE_GRAMMAR_VERSION,
        "target_grammar_version": GRAMMAR_VERSION,
        "node_hash": node_hash(expression),
        "script_hash": None,
        "signal": SIGNAL_NAME,
        "inputs": inputs,
        "nodes": nodes,
    }
    return BridgedScript(expression, source, parse(source), compat_map)


def compile_cond_v2(
    expression: str, *, registry_version: str, registry: IndicatorRegistry = DEFAULT_REGISTRY
) -> CompiledCondV2:
    """Conversion + DSL-12 pre-pipeline compile. Fills
    `compat_map["script_hash"]` so the artifact dict field can be stored
    alongside the original `node_hash` (§3.3)."""
    bridged = bridge_cond_v2(expression, registry=registry)
    compiled = compile_source(bridged.source, registry_version=registry_version)
    compat_map = dict(bridged.compat_map, script_hash=compiled.script_hash)
    return CompiledCondV2(compiled=compiled, compat_map=compat_map)


# ---- Internal helpers ----


def _split(expression: str) -> tuple[str, list[str]]:
    """Branching same as condition_evaluator.evaluate: AND-first, else OR, else single."""
    for joiner in _JOINERS:
        if joiner in expression:
            return joiner, expression.split(joiner)
    return "", [expression]


def _parse_atom(atomic: str, index: int, offset: int, registry: IndicatorRegistry) -> _Atom:
    def reject(code: str, message: str) -> CondV2BridgeError:
        return CondV2BridgeError(code, message, offset=offset, part_index=index)

    m = _ATOMIC_RE.match(atomic)
    if m is None:
        raise reject("STRATEGY_CONDITION_SYNTAX", f"조건 조각을 해석할 수 없습니다: {atomic!r}")
    key, op, threshold = m["key"], m["op"], m["threshold"]
    km = _KEY_RE.match(key)
    if km is None:
        raise reject("STRATEGY_CONDITION_SYNTAX", f"지표 키를 해석할 수 없습니다: {key!r}")
    indicator = km["indicator"]
    try:
        spec = registry.get(indicator)
    except IndicatorError as exc:
        raise reject(exc.code, f"레지스트리에 없는 지표: {indicator!r}") from exc
    if indicator not in _TA_IDENT or len(spec.outputs) != 1:
        raise reject(
            "SCRIPT_COMPAT_UNSUPPORTED",
            f"{indicator!r}는 DSL ta.* 대응이 확정되지 않아 변환하지 않습니다(다중 출력/미매핑)",
        )
    raw = {name: int(value) for name, value in _PARAM_RE.findall(km["params"])}
    unknown = sorted(set(raw) - {p.name for p in spec.params})
    if unknown:
        raise reject("STRATEGY_PARAM_OUT_OF_RANGE", f"{indicator!r}에 없는 파라미터: {unknown}")
    try:
        resolved = registry.validate_params(indicator, raw)
    except IndicatorError as exc:
        raise reject(exc.code, f"{indicator!r} 파라미터 범위 밖: {raw}") from exc
    params = tuple(resolved[p.name] for p in spec.params)
    return _Atom(index, offset, key, op, threshold, indicator, params, tuple(spec.inputs))


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
