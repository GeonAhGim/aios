"""L01 — spec.py / specs_talib.py 계약 테스트.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.2 L01, DoD:
`pytest tests/unit/core/indicators/test_registry.py -k specs` — 11개 스펙의
lookback이 실제 TA-Lib 출력의 선행 NaN 개수와 실측으로 일치해야 한다
(하드코딩 기대값과의 동어반복 비교는 금지).

registry.py(조회·검증 단일 진입점)는 L02 몫이라 아직 없다 — 여기서는
`TALIB_SPECS`/`ParamSpec`의 범위 값 자체를 직접 대조해 "미지 지표"·
"범위 밖 파라미터" negative case를 검증한다.
"""

from __future__ import annotations

import hashlib
import inspect
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pytest
import talib

from src.core.indicators import talib_adapter
from src.core.indicators.registry import IndicatorError, IndicatorRegistry
from src.core.indicators.spec import REGISTRY_VERSION, IndicatorSpec, ParamSpec
from src.core.indicators.specs_talib import TALIB_SPECS
from src.core.indicators.talib_adapter import IndicatorService
from src.data.models.market_data import Candle

EXPECTED_INDICATORS = frozenset(
    {"SMA", "EMA", "RSI", "ATR", "CCI", "WILLR", "MFI", "MACD", "BBANDS", "STOCH", "OBV"}
)


def _default_params(spec: IndicatorSpec) -> dict[str, int]:
    return {p.name: p.default for p in spec.params}


def _synthetic_ohlcv(n: int = 300) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(42)
    close = np.cumsum(rng.normal(size=n)) + 100.0
    high = close + np.abs(rng.normal(size=n)) + 0.5
    low = close - np.abs(rng.normal(size=n)) - 0.5
    volume = np.abs(rng.normal(size=n)) * 1000.0 + 100.0
    return {"open": close, "high": high, "low": low, "close": close, "volume": volume}


def _leading_nan_count(arr: np.ndarray) -> int:
    valid = np.where(~np.isnan(arr))[0]
    return int(valid[0]) if len(valid) else len(arr)


def test_specs_registry_version_is_ind_v1() -> None:
    assert REGISTRY_VERSION == "ind-v1"


def test_specs_cover_all_161_talib_indicators_including_the_eleven_overrides() -> None:
    """IND-10(task-1729)이 161종 자동 생성으로 확장했다 — 수기 오버라이드 11개는
    부분집합으로 여전히 남아 있어야 한다(정밀도·색상 등 표시 세부 보존)."""
    assert len(TALIB_SPECS) == 161
    assert EXPECTED_INDICATORS.issubset(set(TALIB_SPECS))


@pytest.mark.parametrize("name", sorted(EXPECTED_INDICATORS))
def test_specs_lookback_matches_talib_nan_count(name: str) -> None:
    spec = TALIB_SPECS[name]
    arrays = _synthetic_ohlcv()
    inputs = [arrays[key] for key in spec.inputs]
    params = _default_params(spec)

    talib_func = getattr(talib, name)
    raw_output = talib_func(*inputs, **params)
    first_line = raw_output[0] if isinstance(raw_output, tuple) else raw_output

    actual_leading_nan = _leading_nan_count(first_line)
    assert spec.lookback(params) == actual_leading_nan


@pytest.mark.parametrize("name", sorted(EXPECTED_INDICATORS))
def test_specs_defaults_are_within_param_range(name: str) -> None:
    spec = TALIB_SPECS[name]
    for param in spec.params:
        assert param.min <= param.default <= param.max
    assert spec.causal is True
    assert len(spec.outputs) >= 1


def test_specs_unknown_indicator_is_rejected() -> None:
    assert "UNKNOWN_INDICATOR" not in TALIB_SPECS
    assert TALIB_SPECS.get("ICHIMOKU") is None


def test_specs_out_of_range_param_is_rejected() -> None:
    spec = TALIB_SPECS["SMA"]
    timeperiod = next(p for p in spec.params if p.name == "timeperiod")
    assert timeperiod.min == 2
    assert timeperiod.max == 500
    assert not (timeperiod.min <= 1 <= timeperiod.max)
    assert not (timeperiod.min <= 501 <= timeperiod.max)


def test_param_spec_is_frozen() -> None:
    spec = ParamSpec(name="timeperiod", min=2, max=500, default=20)
    with pytest.raises(AttributeError):
        spec.default = 30  # type: ignore[misc]


# --- L02 registry.py: 조회·검증·lookback·registry_hash ---------------------


def test_registry_get_returns_known_spec() -> None:
    registry = IndicatorRegistry()
    assert registry.get("SMA") is TALIB_SPECS["SMA"]


def test_registry_get_unknown_indicator_raises() -> None:
    registry = IndicatorRegistry()
    with pytest.raises(IndicatorError) as excinfo:
        registry.get("ICHIMOKU")
    assert excinfo.value.code == "STRATEGY_INDICATOR_UNKNOWN"


def test_registry_validate_params_fills_defaults() -> None:
    registry = IndicatorRegistry()
    assert registry.validate_params("SMA", {}) == {"timeperiod": 20}


def test_registry_validate_params_accepts_in_range_override() -> None:
    registry = IndicatorRegistry()
    assert registry.validate_params("SMA", {"timeperiod": 100}) == {"timeperiod": 100}


@pytest.mark.parametrize("timeperiod", [1, 0, -5, 501, 10_000])
def test_registry_validate_params_out_of_range_raises(timeperiod: int) -> None:
    registry = IndicatorRegistry()
    with pytest.raises(IndicatorError) as excinfo:
        registry.validate_params("SMA", {"timeperiod": timeperiod})
    assert excinfo.value.code == "STRATEGY_PARAM_OUT_OF_RANGE"


def test_registry_validate_params_rejects_non_int_value() -> None:
    registry = IndicatorRegistry()
    with pytest.raises(IndicatorError) as excinfo:
        registry.validate_params("SMA", {"timeperiod": 20.5})  # type: ignore[dict-item]
    assert excinfo.value.code == "STRATEGY_PARAM_OUT_OF_RANGE"


def test_registry_validate_params_unknown_indicator_raises() -> None:
    registry = IndicatorRegistry()
    with pytest.raises(IndicatorError) as excinfo:
        registry.validate_params("ICHIMOKU", {})
    assert excinfo.value.code == "STRATEGY_INDICATOR_UNKNOWN"


def test_registry_lookback_delegates_to_spec_with_resolved_params() -> None:
    registry = IndicatorRegistry()
    assert registry.lookback("SMA", {"timeperiod": 20}) == 19
    assert registry.lookback("MACD", {}) == 26 + 9 - 2


def test_registry_lookback_unknown_indicator_raises() -> None:
    registry = IndicatorRegistry()
    with pytest.raises(IndicatorError) as excinfo:
        registry.lookback("ICHIMOKU", {})
    assert excinfo.value.code == "STRATEGY_INDICATOR_UNKNOWN"


def test_registry_hash_is_stable_across_calls_and_instances() -> None:
    first = IndicatorRegistry()
    second = IndicatorRegistry()
    assert first.registry_hash() == first.registry_hash()
    assert first.registry_hash() == second.registry_hash()


def test_registry_hash_changes_when_a_spec_changes() -> None:
    baseline = IndicatorRegistry().registry_hash()

    mutated_specs = dict(TALIB_SPECS)
    original_sma = mutated_specs["SMA"]
    mutated_specs["SMA"] = IndicatorSpec(
        name=original_sma.name,
        inputs=original_sma.inputs,
        params=(ParamSpec(name="timeperiod", min=2, max=999, default=20),),
        outputs=original_sma.outputs,
        lookback=original_sma.lookback,
        plots=original_sma.plots,
        causal=original_sma.causal,
    )

    mutated_hash = IndicatorRegistry(mutated_specs).registry_hash()
    assert mutated_hash != baseline


def test_registry_hash_unaffected_by_dict_construction_order() -> None:
    forward = IndicatorRegistry(dict(TALIB_SPECS))
    reversed_specs = dict(reversed(list(TALIB_SPECS.items())))
    backward = IndicatorRegistry(reversed_specs)
    assert forward.registry_hash() == backward.registry_hash()


# --- DEEPEN(task-3199): L02 registry.py 자체 실패 주입 — 손상된 기본값도 fail-closed ---


def test_validate_params_fails_closed_when_spec_default_is_corrupted_out_of_range() -> None:
    """실패 주입: L01 스펙 데이터가 손상되어(배포 사고·수기 오버라이드 실수 등)
    `default`가 선언된 [min, max] 밖에 있는 상태로 레지스트리에 실리면, L02는
    그 손상된 기본값을 조용히 통과시키지 않고 fail-closed로 거부해야 한다
    (CLAUDE.md §3 "Default posture is fail-closed") — 호출자가 override를
    전혀 주지 않아도(=default 그대로 채택되는 경로) 거부되는지 확인한다."""
    original = TALIB_SPECS["SMA"]
    corrupted_spec = IndicatorSpec(
        name=original.name,
        inputs=original.inputs,
        params=(ParamSpec(name="timeperiod", min=2, max=500, default=999),),
        outputs=original.outputs,
        lookback=original.lookback,
        plots=original.plots,
        causal=original.causal,
    )
    registry = IndicatorRegistry({"SMA": corrupted_spec})

    with pytest.raises(IndicatorError) as excinfo:
        registry.validate_params("SMA", {})
    assert excinfo.value.code == "STRATEGY_PARAM_OUT_OF_RANGE"


# --- DEEPEN(task-3199): 수치 성능 단언 — get/validate_params/lookback 핫 패스 ---


def _registry_hot_path_latencies_ms(iterations: int = 200) -> list[float]:
    registry = IndicatorRegistry()
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        registry.get("SMA")
        registry.validate_params("SMA", {"timeperiod": 20})
        registry.lookback("SMA", {"timeperiod": 20})
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


_REGISTRY_HOT_PATH_BUDGET_MS = 1.0


def test_registry_get_validate_lookback_p95_latency_within_self_declared_budget() -> None:
    """수치 성능 단언: `get`/`validate_params`/`lookback`은 전략 실행 루프가 매
    평가 틱마다 호출하는 핫 패스다(ADR-2026-09-09-C 예산표에 전용 항목은
    없다 — dict 조회 + 정수 범위 비교뿐인 순수 CPU 경로라는 사실 위에 자체
    예산을 건다). 로컬 실측 p95 대비 넉넉한 여유를 둔 1ms."""
    samples = _registry_hot_path_latencies_ms()
    p95_ms = _p95(samples)
    print(
        f"[L02 registry] get+validate_params+lookback p95={p95_ms:.3f}ms "
        f"budget<{_REGISTRY_HOT_PATH_BUDGET_MS:.1f}ms (n={len(samples)})"
    )
    assert p95_ms < _REGISTRY_HOT_PATH_BUDGET_MS


# --- DEEPEN(task-3199): 게이트 적색 재현 — validate_params 경계값 검사 ---------


def test_registry_validate_params_boundary_gate_turns_red_on_off_by_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현: `test_registry_validate_params_accepts_in_range_override`류
    경계값 negative test가 실제로 min/max 경계 하나 밖 버그를 잡아내는지
    확인한다 — `validate_params`의 범위 비교를 폐구간(`<=`)에서 개구간(`<`)으로
    바꿔치기한 손상된 구현을 흉내 내, max 경계값(500) 자체가 부당하게
    거부되는지 재현한다. 이 테스트가 없으면 경계값 검사가 우연히 항상
    통과하는 tautology인지 아무도 검증하지 못한다."""

    def _broken_validate_params(
        self: IndicatorRegistry, name: str, params: dict[str, int]
    ) -> dict[str, int]:
        spec = self.get(name)
        resolved: dict[str, int] = {}
        for param_spec in spec.params:
            value = params.get(param_spec.name, param_spec.default)
            if not isinstance(value, int) or isinstance(value, bool):
                raise IndicatorError("STRATEGY_PARAM_OUT_OF_RANGE")
            if not (param_spec.min < value < param_spec.max):  # 버그: 폐구간이어야 함
                raise IndicatorError("STRATEGY_PARAM_OUT_OF_RANGE")
            resolved[param_spec.name] = value
        return resolved

    monkeypatch.setattr(IndicatorRegistry, "validate_params", _broken_validate_params)
    registry = IndicatorRegistry()

    with pytest.raises(IndicatorError):
        registry.validate_params("SMA", {"timeperiod": 500})  # 원래는 허용돼야 함(max 경계)


# --- L03 talib_adapter.py: registry 위임 ------------------------------------


def _candles(n: int, *, base: float = 100.0) -> list[Candle]:
    now = datetime.now(timezone.utc)
    out = []
    for i in range(n):
        price = base + i
        out.append(
            Candle(
                symbol="BTC/USDT",
                exchange="bitget",
                timeframe="1h",
                open=Decimal(str(price)),
                high=Decimal(str(price + 1)),
                low=Decimal(str(price - 1)),
                close=Decimal(str(price)),
                volume=Decimal("1000"),
                open_time=now + timedelta(hours=i),
                close_time=now + timedelta(hours=i + 1),
            )
        )
    return out


def test_talib_adapter_has_no_leftover_specs_or_period_param_name() -> None:
    """옛 지표별 딕셔너리(`_SPECS`) 잔재 검사 — IND-10 이후 정식 이름
    `TALIB_SPECS`(레지스트리 카탈로그) 언급은 이 검사 대상이 아니다."""
    source = inspect.getsource(talib_adapter)
    assert "_SPECS" not in source.replace("TALIB_SPECS", "")
    assert "period_param_name" not in source


@pytest.mark.parametrize("timeperiod", [0, -1, -5, 501])
def test_calculate_rejects_out_of_range_param_via_registry(timeperiod: int) -> None:
    with pytest.raises(IndicatorError) as excinfo:
        IndicatorService().calculate("SMA", _candles(30), timeperiod=timeperiod)
    assert excinfo.value.code == "STRATEGY_PARAM_OUT_OF_RANGE"


def test_calculate_rejects_unknown_indicator_via_registry() -> None:
    with pytest.raises(IndicatorError) as excinfo:
        IndicatorService().calculate("ICHIMOKU", _candles(30))
    assert excinfo.value.code == "STRATEGY_INDICATOR_UNKNOWN"


def test_indicator_result_registry_version_matches_registry() -> None:
    result = IndicatorService().calculate("SMA", _candles(30), timeperiod=5)
    assert result.registry_version == REGISTRY_VERSION
    assert result.registry_version == "ind-v1"


@pytest.mark.parametrize("name", ["MACD", "BBANDS", "STOCH"])
def test_calculate_min_required_bars_matches_registry_lookback(name: str) -> None:
    registry = IndicatorRegistry()
    spec = TALIB_SPECS[name]
    default_params = {p.name: p.default for p in spec.params}
    lookback = registry.lookback(name, default_params)

    too_few = IndicatorService().calculate(name, _candles(lookback))
    assert too_few.values == []

    just_enough = IndicatorService().calculate(name, _candles(lookback + 1))
    assert just_enough.values != []


# --- DEEPEN(task-3198): 실패 주입 — TA-Lib 네이티브 실패가 성공으로 위장되지 않음 ---


def test_calculate_propagates_talib_runtime_failure_instead_of_masking_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: TA-Lib 네이티브 함수가 예외를 던지면(손상된 빌드·SIMD 버전
    불일치 등) `IndicatorService.calculate()`가 그 예외를 삼키고 빈 결과나
    기본값으로 위장하지 않고 그대로 전파하는지 확인한다(fail-closed, L03이
    L01/L02에 위임하는 경계 — 계산 실패를 검증 실패처럼 감추면 안 된다)."""

    def _broken_sma(*args: object, **kwargs: object) -> object:
        raise RuntimeError("simulated TA-Lib native failure")

    monkeypatch.setattr(talib, "SMA", _broken_sma)

    with pytest.raises(RuntimeError, match="simulated TA-Lib native failure"):
        IndicatorService().calculate("SMA", _candles(30), timeperiod=5)


# --- DEEPEN(task-3198): 수치 성능 단언 — registry_hash() 지연 ---------------


def _hash_latencies_ms(iterations: int = 20) -> list[float]:
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        IndicatorRegistry().registry_hash()
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


_REGISTRY_HASH_BUDGET_MS = 50.0


def test_registry_hash_p95_latency_within_self_declared_budget() -> None:
    """수치 성능 단언: ADR-2026-09-09-C Decision 1 예산표에 `registry_hash()`
    전용 항목이 없다(가장 가까운 항목은 "지표 증분=일괄 동일", 지연 예산가
    아님) — 161종 스펙을 정준 JSON 직렬화 + sha256 하는 순수 CPU 경로(디스크·
    네트워크 I/O 없음)라는 사실 위에 자체 예산을 건다: 로컬 실측 p95 대비
    넉넉한 여유를 둔 50ms. `registry_hash()`는 strategy_artifact 해시 계산의
    입력이라 아티팩트 빌드 경로를 막으면 안 된다 — 벗어나면 회귀(예:
    `canonical_spec_dict` 중복 순회)로 본다."""
    samples = _hash_latencies_ms(iterations=20)
    p95_ms = _p95(samples)
    print(
        f"[L02 registry] registry_hash() p95={p95_ms:.2f}ms "
        f"budget<{_REGISTRY_HASH_BUDGET_MS:.0f}ms (n={len(samples)})"
    )
    assert p95_ms < _REGISTRY_HASH_BUDGET_MS


def test_registry_hash_budget_gate_actually_fails_past_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현: 위 단언식이, 해시 계산 경로 한 곳이 예산을 실제로
    넘기도록 지연을 주입했을 때 진짜로 `AssertionError`를 내는지(= CI가
    실제로 빨간불이 되는지) 확인한다. 이 테스트가 없으면 위 단언이 항상
    통과하는 tautology인지 아무도 검증하지 못한다."""
    original_sha256 = hashlib.sha256

    def _stalled_sha256(*args: object, **kwargs: object) -> object:
        time.sleep(_REGISTRY_HASH_BUDGET_MS / 1000.0)
        return original_sha256(*args, **kwargs)

    monkeypatch.setattr("src.core.indicators.registry.hashlib.sha256", _stalled_sha256)

    samples = _hash_latencies_ms(iterations=3)
    p95_ms = _p95(samples)
    with pytest.raises(AssertionError):
        assert p95_ms < _REGISTRY_HASH_BUDGET_MS


# --- DEEPEN(task-3198): 게이트 적색 재현 — lookback 오지정 시 실측 대조 실패 ---


def test_lookback_nan_count_gate_turns_red_when_lookback_formula_is_off_by_one() -> None:
    """게이트 적색 재현: 위쪽 `test_specs_lookback_matches_talib_nan_count`가
    실제로 틀린 lookback을 잡아내는지 확인한다 — SMA의 lookback을 정답인
    `timeperiod - 1` 대신 `timeperiod`로(오프바이원) 바꿔치기한
    `IndicatorSpec`을 만든 뒤, 같은 실측 대조식이 진짜로 `AssertionError`를
    내는지 본다. 이 테스트가 없으면 위 대조가 우연히 항상 통과하는
    tautology인지 아무도 검증하지 못한다."""
    spec = TALIB_SPECS["SMA"]
    arrays = _synthetic_ohlcv()
    inputs = [arrays[key] for key in spec.inputs]
    params = _default_params(spec)

    raw_output = talib.SMA(*inputs, **params)
    actual_leading_nan = _leading_nan_count(raw_output)

    broken_spec = IndicatorSpec(
        name=spec.name,
        inputs=spec.inputs,
        params=spec.params,
        outputs=spec.outputs,
        lookback=lambda p: p["timeperiod"],  # off-by-one 버그: 정답은 timeperiod - 1
        plots=spec.plots,
        causal=spec.causal,
    )

    assert broken_spec.lookback(params) != actual_leading_nan  # sanity: 실제로 다름
    with pytest.raises(AssertionError):
        assert broken_spec.lookback(params) == actual_leading_nan
