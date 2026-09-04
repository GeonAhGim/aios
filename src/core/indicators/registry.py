"""L02 — 지표 조회·파라미터 검증·lookback·registry_hash 단일 진입점.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.2 L02

순수 모듈 — I/O 없음, L01(`spec`, `specs_talib`)만 소비하고 지표 계산은
하지 않는다. `registry_hash()`는 `strategy_artifact.registry_version`의
입력이 되므로(§7 아티팩트 해시 규칙) dict 순서·부동소수에 의존하지 않게
스펙 이름으로 정렬한 뒤 `sort_keys` JSON으로 정준 직렬화한다. `lookback`
콜러블 자체(함수 객체)는 해시에 넣을 수 없으므로 `__name__`으로 대신한다 —
동일 모듈에서 재기동해도 같은 이름이 나오므로 프로세스 재기동에 안정적이다.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from src.core.indicators.spec import IndicatorSpec
from src.core.indicators.specs_talib import TALIB_SPECS


class IndicatorError(Exception):
    """레지스트리 조회/검증 실패. `code`는 API 계층이 400 매핑에 쓴다."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class IndicatorRegistry:
    """지표 스펙 조회·파라미터 검증·lookback 산출 단일 진입점."""

    def __init__(self, specs: Mapping[str, IndicatorSpec] | None = None) -> None:
        self._specs: dict[str, IndicatorSpec] = (
            dict(specs) if specs is not None else dict(TALIB_SPECS)
        )

    def get(self, name: str) -> IndicatorSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise IndicatorError("STRATEGY_INDICATOR_UNKNOWN") from None

    def validate_params(self, name: str, params: Mapping[str, int]) -> dict[str, int]:
        spec = self.get(name)
        resolved: dict[str, int] = {}
        for param_spec in spec.params:
            value = params.get(param_spec.name, param_spec.default)
            if not isinstance(value, int) or isinstance(value, bool):
                raise IndicatorError("STRATEGY_PARAM_OUT_OF_RANGE")
            if not (param_spec.min <= value <= param_spec.max):
                raise IndicatorError("STRATEGY_PARAM_OUT_OF_RANGE")
            resolved[param_spec.name] = value
        return resolved

    def lookback(self, name: str, params: Mapping[str, int]) -> int:
        spec = self.get(name)
        resolved = self.validate_params(name, params)
        return spec.lookback(resolved)

    def registry_hash(self) -> str:
        canonical = [
            {
                "name": name,
                "inputs": list(spec.inputs),
                "params": [
                    {
                        "name": param_spec.name,
                        "min": param_spec.min,
                        "max": param_spec.max,
                        "default": param_spec.default,
                    }
                    for param_spec in spec.params
                ],
                "outputs": list(spec.outputs),
                "causal": spec.causal,
                "lookback": spec.lookback.__name__,
            }
            for name, spec in sorted(self._specs.items())
        ]
        payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


DEFAULT_REGISTRY = IndicatorRegistry()
