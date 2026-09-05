"""L4_analytics_authoring_backtest_marketplace_v1.0.md §3.3/§9.4 DSL-9(a) —
AIOS Script `ta.*` 내장함수: 지표 레지스트리(L02) 버전 고정 + IND-1 증분 엔진 위임.

순수 모듈(I/O 없음). 이 파일 안에는 지표 산식이 없다(I-04 단일출처): 스펙 조회·
파라미터 검증·lookback은 `src/core/indicators/registry.py`(L02), 계산은
`engine/incremental.py`(IND-1 `IncrementalIndicator`)에 그대로 위임한다. 테스트가
엔진 호출을 스파이로 증명한다(I-10).

레지스트리 버전 고정(§3.3 "`ta.*`는 IND 레지스트리 버전에 고정"):
- `TaBuiltins.registry_version` = `IndicatorRegistry.registry_hash()`(스펙 정준 해시,
  L02 88fbbbf). DSL-12 `artifact/hash.py`의 `registry_version` 입력과 같은 문자열이라
  "같은 script_hash = 같은 레지스트리로 계산"이 성립한다.
- 컴파일 산출물이 기록한 버전을 `expected_registry_version`으로 넘기면 현재
  레지스트리 해시와 다를 때 표를 만들지 않고 거부한다(`INDICATOR_REGISTRY_MISMATCH`,
  fail-closed — 다른 레지스트리로 조용히 계산하지 않는다).
- 호출마다 `TaCall`(지표·출력·검증된 파라미터·lookback·registry_hash)을 `calls`
  장부에 남긴다. `Series`는 값만 담는 불변 타입이라 메타데이터를 값에 붙이지 않고
  장부로 기록한다(백테스트 리포트·감사 증거 입력).

호출 규약 `ta.<ident>(<inputs...>, <params...>)`:
- ident는 레지스트리에서 파생한다: 단일 출력 지표는 소문자 이름(`ta.sma`), 다중
  출력 지표는 `<이름>_<출력>` 전부(`ta.macd_signal`·`ta.bbands_upperband`·
  `ta.stoch_slowd`)와 첫 출력의 별칭인 소문자 이름(`ta.macd` = macd 라인).
- 앞 `len(spec.inputs)`개 인자는 입력 시리즈(spec.inputs 순서: `ta.atr(high, low,
  close, 14)`). 스칼라가 오면 봉 수로 편다(`close[1]`처럼 정적 타입은 float이지만
  런타임은 시리즈인 값이 정당하므로 DSL-8 `broadcast`와 같은 규칙).
- 뒤 인자는 파라미터(spec.params 순서). 생략하면 레지스트리 기본값. 각 값은 스칼라
  int여야 하고(bool·float·시리즈 거부) 범위는 레지스트리가 검증한다.
- na: 입력의 *선행* na 구간(`[n]` 시프트가 만드는 접두)은 건너뛰고 첫 완전 봉부터
  엔진에 흘린다(그 구간 출력은 na). 접두 이후의 내부 na는 `INDICATOR_INPUT_INVALID`
  거부(0 대체 금지). 엔진에 흘릴 봉 수가 lookback 이하라 값이 하나도 나올 수 없으면
  `INDICATOR_LOOKBACK_INSUFFICIENT` 거부(전부-na 시리즈로 폴백하지 않는다).

오류는 전부 `BuiltinCallError`(`ScriptRuntimeError` 하위, `reason`=코드). 레지스트리·
엔진의 `IndicatorError.code`는 그대로 `reason`으로 옮긴다.
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
    from src.core.script.runtime.interpreter import Builtin, CallSite

__all__ = ["TaBuiltins", "TaCall", "default_builtins", "ta_idents"]

_NS = "ta"


@dataclass(frozen=True)
class TaCall:
    """`ta.*` 호출 한 건의 장부 항목(레지스트리 버전 고정 증거)."""

    ident: str
    indicator: str
    output: str
    params: tuple[tuple[str, int], ...]
    lookback: int
    registry_hash: str
    bar_count: int
    fed_from: int
    """엔진에 흘린 첫 봉 인덱스(선행 na 접두 길이)."""


def ta_idents(spec: IndicatorSpec) -> dict[str, str]:
    """스펙 하나가 노출하는 `ident → output` 표(모듈 docstring 규약)."""
    lowered = spec.name.lower()
    if spec.outputs == ("value",):
        return {lowered: "value"}
    table = {f"{lowered}_{output}": output for output in spec.outputs}
    table[lowered] = spec.outputs[0]
    return table


class TaBuiltins:
    """레지스트리 하나에 고정된 `ta.*` 빌트인 표 + 호출 장부."""

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

    # ---- 호출 ----

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
    """DSL-9a 내장 테이블 등록: `math.*`(MATH_BUILTINS) + `ta.*`(레지스트리 고정).

    `registry_version`(컴파일 산출물이 기록한 레지스트리 해시)을 주면 현재
    레지스트리와 다를 때 `TaBuiltins`가 거부한다. 호출 장부가 필요하면 `TaBuiltins`를
    직접 만들어 `.table`을 합친다. `interpreter.py`는 지표 레지스트리를 임포트하지
    않으므로(DSL-8 순수성 정적 검사) 이 진입점은 여기 둔다.
    """
    ta = TaBuiltins(registry, expected_registry_version=registry_version)
    return {**MATH_BUILTINS, **ta.table}


def registry_names(registry: IndicatorRegistry) -> tuple[str, ...]:
    """레지스트리에 등록된 지표 이름. L02가 열거 API를 두지 않아 스펙 사전을 읽는다."""
    specs: Mapping[str, IndicatorSpec] = registry._specs  # L02 열거 API 부재(읽기 전용)
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
    """모든 입력이 non-na인 첫 봉 인덱스(없으면 bar_count)."""
    for t in range(bar_count):
        if all(col[t] is not None for col in columns.values()):
            return t
    return bar_count

