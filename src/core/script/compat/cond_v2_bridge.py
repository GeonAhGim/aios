"""L4_analytics_authoring_backtest_marketplace_v1.0.md §2.4/§3.3/§9.4 DSL-10 —
cond-v2 조건식 → AIOS Script(DSL-1 AST) 변환 브리지 + `compat_map`.

입력은 `src/core/strategy/condition_evaluator.py`(FROZEN_PAPER_ONLY, 읽기
전용)가 해석하는 cond-v2 v1 평면 문자열 — `ConditionCompiler`가 만드는
`"{KEY} {OP} {NUMBER}"`를 `" AND "` 또는 `" OR "` 하나로만 결합한 형태다.
L4_strategy §3.1의 `condition_ast.py`/`condition_parser.py`(괄호·NOT·`@tf`)
는 아직 저장소에 없어, 이 브리지는 현 평가기가 실제로 받아들이는 문법만
받아들이고 그 밖은 위치(문자 오프셋·조각 번호)와 함께 거부한다(부분 변환
금지). 출력은 소스 텍스트를 만들어 DSL-3 `parse()`로 다시 읽은 `Program`
이라 결과가 §3.3 문법 안에 있음이 구성적으로 보장된다(새 문법 없음).

변환 규칙(§3.3 "cond-v2 호환" 예시 그대로):
    RSI_timeperiod14 < 30 AND SMA_timeperiod20 > 100
  → input close: series<float> = 0
    let RSI_timeperiod14 = ta.rsi(close, 14)
    let SMA_timeperiod20 = ta.sma(close, 20)
    signal cond = RSI_timeperiod14 < 30 and SMA_timeperiod20 > 100
지표 이름·파라미터·입력 시리즈는 `indicators.registry.DEFAULT_REGISTRY`
(cond-v2 실행 루프 `market_state.py`가 쓰는 것과 같은 레지스트리)로 검증하고
기본값을 채운다. `ta.*` 인자 순서는 레지스트리 `params` 선언 순서다 —
미검증: DSL-9 `builtins_ta.py`가 확정되면 `_TA_IDENT`만 맞추면 된다.
다중 출력 지표(MACD·BBANDS·STOCH)는 cond-v2가 primary 출력을 암묵 선택하지만
DSL 호출의 반환 출력이 미정이라 거부한다(추측으로 신호를 만들지 않는다).

의미 차이(둘 다 "비발화" 범위 안, 테스트가 명시 단언):
- cond-v2는 좌→우 단락 평가로 첫 누락 키에서 판단 보류(예외)하지만 DSL은
  Kleene 3치(`na and False = False`)다. 발화(True)는 항상 일치한다.
- 교차 연산은 직전 틱이 없으면 cond-v2가 False, DSL은 na(0번 봉)다.

`compat_map`은 컴파일 아티팩트에 실리는 순수 dict다(DB 저장·마이그레이션
없음): 원 노드 id(`root`, `atom:i`) → 스크립트 라인·식별자, 원 표현식의
`node_hash`(= sha256(정규화 문자열), L4_strategy §3.1 `to_canonical` 규칙:
공백 단일화·리프 순서 보존), 변환 결과 `script_hash`(`compile_cond_v2`).
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

# condition_evaluator._ATOMIC_RE / market_state._KEY_RE와 동일 문법(테스트가 동기화 단언).
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
# 레지스트리 이름 → DSL `ta.` 식별자(미검증: DSL-9 확정 시 이 표만 수정).
_TA_IDENT: Final[Mapping[str, str]] = {
    n: n.lower() for n in ("SMA", "EMA", "RSI", "ATR", "CCI", "WILLR", "MFI", "OBV")
}


class CondV2BridgeError(Exception):
    """변환 거부(fail-closed). `offset`은 원 cond-v2 문자열의 0-기반 문자 위치,
    `part_index`는 결합 조각 번호. `code`는 원인 분류(cond-v2/레지스트리 코드 재사용)."""

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
    """`source`는 DSL 소스 텍스트, `program == parse(source)`, `compat_map`은 JSON 호환 dict."""

    expression: str
    source: str
    program: Program
    compat_map: dict[str, Any]


@dataclass(frozen=True)
class CompiledCondV2:
    compiled: CompiledScript
    compat_map: dict[str, Any]


def canonical_expression(expression: str) -> str:
    """L4_strategy §3.1 `to_canonical`(평면형): 공백 단일화, 리프 순서 보존."""
    joiner, parts = _split(expression)
    canon = [" ".join(p.strip().split()) for p in parts]
    return joiner.join(canon) if joiner else canon[0]


def node_hash(expression: str) -> str:
    """`sha256(to_canonical(expr))` — L4_strategy §3.0 표의 `node_hash` 정의."""
    return hashlib.sha256(canonical_expression(expression).encode("utf-8")).hexdigest()


def bridge_cond_v2(
    expression: str, *, registry: IndicatorRegistry = DEFAULT_REGISTRY
) -> BridgedScript:
    """cond-v2 문자열 → `BridgedScript`. 미지 연산자·지원 밖 지표/파라미터·혼합
    결합·다중 출력 지표는 `CondV2BridgeError`(위치 포함)로 전체 거부한다."""
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
    """변환 + DSL-12 전 파이프라인 컴파일. `compat_map["script_hash"]`를 채워 원
    `node_hash`와 함께 아티팩트 dict 필드로 저장할 수 있게 한다(§3.3)."""
    bridged = bridge_cond_v2(expression, registry=registry)
    compiled = compile_source(bridged.source, registry_version=registry_version)
    compat_map = dict(bridged.compat_map, script_hash=compiled.script_hash)
    return CompiledCondV2(compiled=compiled, compat_map=compat_map)


# ---- 내부 ----


def _split(expression: str) -> tuple[str, list[str]]:
    """condition_evaluator.evaluate와 같은 분기: AND 우선, 아니면 OR, 아니면 단일."""
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
