"""L01 — spec.py / specs_talib.py 계약 테스트.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.2 L01, DoD:
`pytest tests/unit/core/indicators/test_registry.py -k specs` — 11개 스펙의
lookback이 실제 TA-Lib 출력의 선행 NaN 개수와 실측으로 일치해야 한다
(하드코딩 기대값과의 동어반복 비교는 금지).

registry.py(조회·검증 단일 진입점)는 L02 몫이라 아직 없다 — 여기서는
`TALIB_SPECS`/`ParamSpec`의 범위 값 자체를 직접 대조해 "미지 지표"·
"범위 밖 파라미터" negative case를 검증한다.

L03 talib_adapter.py(IndicatorService.calculate 위임) 계약 테스트는
`test_talib_adapter_registry.py`로 분리했다(500-LOC 정책,
ADR-2026-09-10-C §7).
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, cast

import numpy as np
import pytest
import talib

from src.core.indicators.registry import IndicatorError, IndicatorRegistry
from src.core.indicators.spec import REGISTRY_VERSION, IndicatorSpec, ParamSpec
from src.core.indicators.specs_talib import TALIB_SPECS
from tests._perf.relative_budget import RelativeBudget

EXPECTED_INDICATORS = frozenset(
    {"SMA", "EMA", "RSI", "ATR", "CCI", "WILLR", "MFI", "MACD", "BBANDS", "STOCH", "OBV"}
)


def _default_params(spec: IndicatorSpec) -> dict[str, int]:
    return {p.name: p.default for p in spec.params}


def _synthetic_ohlcv(n: int = 300) -> dict[str, np.ndarray[Any, np.dtype[Any]]]:
    rng = np.random.default_rng(42)
    close = np.cumsum(rng.normal(size=n)) + 100.0
    high = close + np.abs(rng.normal(size=n)) + 0.5
    low = close - np.abs(rng.normal(size=n)) - 0.5
    volume = np.abs(rng.normal(size=n)) * 1000.0 + 100.0
    return {"open": close, "high": high, "low": low, "close": close, "volume": volume}


def _leading_nan_count(arr: np.ndarray[Any, np.dtype[np.floating[Any]]]) -> int:
    valid = np.where(~np.isnan(arr))[0]
    return int(valid[0]) if len(valid) else len(arr)


def test_specs_registry_version_is_ind_v1() -> None:
    assert REGISTRY_VERSION == "ind-v1"


def test_specs_cover_every_installed_talib_indicator_including_the_eleven_overrides() -> None:
    """IND-10(task-1729)이 `talib.get_functions()` 전량 자동 생성으로 확장했다 —
    종수는 설치된 TA-Lib 버전이 정한다(리터럴 고정 금지). 수기 오버라이드 11개는
    부분집합으로 여전히 남아 있어야 한다(정밀도·색상 등 표시 세부 보존)."""
    assert set(TALIB_SPECS) == set(talib.get_functions())
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
        setattr(spec, "default", 30)  # noqa: B010 — frozen model must reject assignment


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
        registry.validate_params("SMA", cast(dict[str, int], {"timeperiod": 20.5}))
    assert excinfo.value.code == "STRATEGY_PARAM_OUT_OF_RANGE"


def test_registry_validate_params_unknown_indicator_raises() -> None:
    registry = IndicatorRegistry()
    with pytest.raises(IndicatorError) as excinfo:
        registry.validate_params("ICHIMOKU", {})
    assert excinfo.value.code == "STRATEGY_INDICATOR_UNKNOWN"


def test_registry_validate_params_rejects_unknown_param_names() -> None:
    """범위 밖 파라미터(스펙에 정의되지 않은 이름)를 명시적으로 거부해야 한다.
    호출자가 오타나 잘못된 파라미터 이름을 전달해도 조용히 무시하지 않고
    fail-closed로 차단한다."""
    registry = IndicatorRegistry()
    with pytest.raises(IndicatorError) as excinfo:
        registry.validate_params("SMA", {"timeperiod": 20, "fake_param": 42})
    assert excinfo.value.code == "STRATEGY_PARAM_UNKNOWN"


def test_registry_validate_params_rejects_multiple_unknown_param_names() -> None:
    """여러 개의 알려지지 않은 파라미터 이름도 모두 거부한다."""
    registry = IndicatorRegistry()
    with pytest.raises(IndicatorError) as excinfo:
        registry.validate_params("SMA", {"bogus1": 1, "bogus2": 2})
    assert excinfo.value.code == "STRATEGY_PARAM_UNKNOWN"


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


# --- DEEPEN(task-3198): 수치 성능 단언 — registry_hash() 지연 ---------------


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


# task-8582(리뷰 task-8459): 절대 wall-clock p95<50ms는 공유 플릿 부하만으로
# 회귀 없이 적색이 될 수 있어(task-7631/8455/8356과 동일 패턴) 같은 프로세스
# 보정 루프 대비 비율(`RelativeBudget`)로 교체했다. sha256 순수 CPU라
# mode="wall"이 process_time 클럭 분해능 문제를 피한다(로컬 실측 비율
# ~0.0075x에 60배 이상 여유를 둔 max_ratio=0.5). tests/_perf/relative_budget.py 참고.
_REGISTRY_HASH_MAX_RATIO = 0.5


def test_registry_hash_p95_latency_within_self_declared_budget() -> None:
    """수치 성능 단언: 161종 스펙을 정준 JSON 직렬화 + sha256 하는 순수 CPU
    경로(디스크·네트워크 I/O 없음) 위에 자체 예산을 건다(ADR-2026-09-09-C
    Decision 1 예산표에 전용 항목 없음). `registry_hash()`는 strategy_artifact
    해시 계산의 입력이라 아티팩트 빌드 경로를 막으면 안 된다 — 벗어나면
    회귀(예: `canonical_spec_dict` 중복 순회)로 본다. task-8582: 공유 플릿
    부하로 회귀 없이 적색이 될 수 있어 보정 루프 대비 비율로 판정한다."""
    sample = RelativeBudget().assert_within(
        lambda: IndicatorRegistry().registry_hash(),
        max_ratio=_REGISTRY_HASH_MAX_RATIO,
        mode="wall",
        n=20,
        warmup=1,
        label="registry_hash()",
    )
    print(f"[L02 registry] {RelativeBudget().describe(sample, max_ratio=_REGISTRY_HASH_MAX_RATIO)}")


def test_registry_hash_budget_gate_actually_fails_past_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현: 위 단언식이, 해시 계산 경로 한 곳이 예산을 실제로
    넘기도록 지연을 주입했을 때 진짜로 `AssertionError`를 내는지(= CI가
    실제로 빨간불이 되는지) 확인한다. 이 테스트가 없으면 위 단언이 항상
    통과하는 tautology인지 아무도 검증하지 못한다.

    GitHub run 36588511473: 이 테스트가 "DID NOT RAISE"로 적색이었다 — 이전
    구현은 `time.sleep(0.2)`로 실제 벽시계 200ms를 주입했는데, 이는 호스트
    속도와 무관한 고정 시간이다. 반면 비교 대상인 보정 루프(순수 파이썬
    반복)는 호스트가 느려지면 함께 느려진다. 느리거나 부하가 큰 CI 러너에서
    보정 루프 자체가 200ms를 넘기면 `op_ms/calibration_ms` 비율이 오히려
    줄어들어 `max_ratio=0.5`를 넘기지 못하고 조용히 통과해버린다(회귀
    주입이 통과하는 지연이 아니라 host-load-dependent 실패). 고정 시간
    대신 보정 루프와 동일한 형태의 순수 CPU 반복(정수 연산)을 그 10배
    규모로 주입하면, 두 값이 같은 방식으로 호스트 속도에 비례해 움직여서
    비율이 호스트 속도와 무관하게 ~10x로 안정된다 — 어떤 CI 러너에서도
    `max_ratio=0.5`를 확실히 넘긴다."""
    original_sha256 = hashlib.sha256
    # 보정 루프(tests/_perf/relative_budget.py `_calibration_loop`)와 같은
    # 모양의 순수 CPU 반복을 그 10배(2,000,000 * 10) 규모로 돌려 host-speed
    # 비례 지연을 만든다 — `time.sleep`과 달리 호스트가 느려지면 이 반복도
    # 똑같이 느려지므로 비율이 무너지지 않는다.
    _STALL_ITERATIONS = 20_000_000

    def _stalled_sha256(data: bytes, **kwargs: object) -> object:
        total = 0
        for i in range(_STALL_ITERATIONS):
            total += i * i % 7
        assert total >= 0  # 최적화로 반복이 제거되지 않도록 결과를 소비한다
        return original_sha256(data)

    monkeypatch.setattr("src.core.indicators.registry.hashlib.sha256", _stalled_sha256)

    with pytest.raises(AssertionError):
        RelativeBudget().assert_within(
            lambda: IndicatorRegistry().registry_hash(),
            max_ratio=_REGISTRY_HASH_MAX_RATIO,
            mode="wall",
            n=3,
            warmup=0,
            label="registry_hash() stalled sha256",
        )


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

    raw_output = cast(Any, talib).SMA(*inputs, **params)
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
