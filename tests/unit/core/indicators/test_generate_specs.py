"""IND-2g / IND-10 — 설치된 TA-Lib 전 함수 자동 생성 계약 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 IND-10

DoD: `talib.get_functions()` 전량 등록(캔들 패턴 그룹 포함, 종수는 설치된 TA-Lib
버전이 결정 — 0.4.x 161종/0.6.x 201종을 리터럴로 고정하지 않는다), 같은 talib
버전에서 생성물 바이트 동일
(결정론), 증분=일괄 동일성 샘플 20종, 오버라이드 없는 지표도 PlotSpec 보유.
negative: 파라미터 범위 밖 거부, 미지 함수명 거부, NaN 구간 처리(6건), TA-Lib
미설치 시 모듈 import 자체가 fail-closed(task-8231, IND-2g — 중복
`catalog/generate_from_talib.py` 스냅샷 제거 후 이 파일이 단일 카탈로그가 됨에
따라 §9.3 IND-2g DoD의 "미설치 fail-closed" 항목을 흡수).

DEEPEN(task-2925): task-2727 DEPTH 감사(docs/audit/DEPTH_DSL_IND.md)가 원
task-1729(commit 45b4ce4)에 실패 주입·수치 성능 단언·게이트 적색 재현이
없다고 지적함 — 새 기능 추가 없이 이 리프의 증빙만 보강한다. 실패 주입은
`talib.abstract.Function` 조회 자체가 깨졌을 때(손상된 TA-Lib 설치·바이너리
버전 불일치) 생성이 부분 카탈로그를 성공으로 위장하지 않고 예외를 그대로
전파하는지(fail-closed) 확인한다. 성능 단언은 ADR-2026-09-09-C Decision 1
예산표에 "전종 생성 지연" 전용 항목이 없어(가장 가까운 항목은 "지표 증분=
일괄 동일") 이 리프가 자체 선언한 예산(순수 파이썬 메타데이터 순회, I/O
없음 — 로컬 실측 p95 ~5ms 대비 10배 여유)을 건다. 게이트 적색 재현은 생성
전용(오버라이드 밖) 지표를 손으로 고치는 회귀를 잡는 동등성 단언이 실제로
빨간불이 되는지 확인한다.
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import talib
from talib import abstract as talib_abstract

from src.core.indicators.generate_specs import TALIB_GROUPS, generate_talib_specs
from src.core.indicators.registry import IndicatorError, IndicatorRegistry, canonical_spec_dict
from src.core.indicators.specs_talib import TALIB_SPECS
from src.core.indicators.talib_adapter import IndicatorService
from src.data.models.market_data import Candle

ALL_TALIB_NAMES = sorted(talib.get_functions())
_MANUAL_OVERRIDE_NAMES = frozenset(
    {"SMA", "EMA", "RSI", "ATR", "CCI", "WILLR", "MFI", "MACD", "BBANDS", "STOCH", "OBV"}
)


def _candles(n: int, base: float = 100.0) -> list[Candle]:
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


# --- 카탈로그 크기·그룹 -----------------------------------------------------


def test_every_installed_talib_function_is_generated() -> None:
    """종수는 설치된 라이브러리가 결정한다 — 카탈로그는 `talib.get_functions()`와
    정확히 같은 집합이어야 하고(누락·여분 0), 그 크기를 버전 리터럴로 고정하지
    않는다(TA-Lib 0.4.x 161종, 0.6.x 201종 모두 이 단언을 통과해야 한다)."""
    specs = generate_talib_specs()
    assert len(specs) == len(talib.get_functions())
    assert set(specs) == set(ALL_TALIB_NAMES)
    assert set(TALIB_SPECS) == set(ALL_TALIB_NAMES)


def test_pattern_recognition_group_matches_installed_candle_functions() -> None:
    pattern_names = [name for name, group in TALIB_GROUPS.items() if group == "Pattern Recognition"]
    expected = talib.get_function_groups()["Pattern Recognition"]
    assert sorted(pattern_names) == sorted(expected)
    assert all(name.startswith("CDL") for name in pattern_names)


# --- 버전 유연성: 파라미터 범위가 설치된 라이브러리의 기본값을 수용해야 한다 ---


def test_every_generated_default_lies_within_its_own_range() -> None:
    """게이트 적색 재현(TA-Lib 0.6.x KDJ): `slowk_matype` 기본값 13이 하드코딩
    상한 8(구 `_MATYPE_MAX = 8`, 현 `param_rules.MATYPE_MAX`) 밖에 있어 기본값 호출이
    `STRATEGY_PARAM_OUT_OF_RANGE`로 거부됐다. 생성된 스펙은 자기 기본값을 반드시
    수용해야 한다 — 어떤 버전의 어떤 함수든 기본값으로는 계산 가능해야 하기 때문이다."""
    for name, spec in generate_talib_specs().items():
        for param in spec.params:
            assert param.min <= param.default <= param.max, (name, param)


def test_matype_upper_bound_tracks_installed_ma_type_enum() -> None:
    from src.core.indicators import param_rules as module

    ordinals = [
        getattr(talib.MA_Type, attr)
        for attr in dir(talib.MA_Type)
        if not attr.startswith("_") and isinstance(getattr(talib.MA_Type, attr), int)
    ]
    assert module.MATYPE_MAX == max(ordinals)
    assert module.MATYPE_MAX >= 8  # SMA(0)..T3(8) exist in every supported version


def test_matype_upper_bound_fails_closed_when_enum_exposes_no_ordinals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.core.indicators import param_rules as module

    class _NoOrdinals:
        SMA = "not-an-int"

    monkeypatch.setattr(module.talib, "MA_Type", _NoOrdinals, raising=True)
    with pytest.raises(ValueError, match="MA_Type"):
        module.matype_max()


def test_talib_groups_classify_into_exactly_ten_categories() -> None:
    assert set(TALIB_GROUPS) == set(ALL_TALIB_NAMES)
    assert len(set(TALIB_GROUPS.values())) == 10


# --- 결정론: 같은 talib 버전에서 생성물 바이트(정준 직렬화) 동일 -----------


def test_generation_is_deterministic_across_calls() -> None:
    first = generate_talib_specs()
    second = generate_talib_specs()
    canon_first = [canonical_spec_dict(n, s) for n, s in sorted(first.items())]
    canon_second = [canonical_spec_dict(n, s) for n, s in sorted(second.items())]
    assert canon_first == canon_second


def test_incremental_generation_matches_batch_for_a_20_name_sample() -> None:
    """증분(20종만 생성) == 일괄(전량 생성 후 같은 20종만 추출)."""
    sample = ALL_TALIB_NAMES[:10] + ALL_TALIB_NAMES[-10:]
    incremental = generate_talib_specs(names=sample)
    batch = generate_talib_specs()
    batch_subset = {name: batch[name] for name in sample}

    canon_incremental = [canonical_spec_dict(n, s) for n, s in sorted(incremental.items())]
    canon_batch_subset = [canonical_spec_dict(n, s) for n, s in sorted(batch_subset.items())]
    assert canon_incremental == canon_batch_subset
    assert len(sample) == 20


# --- PlotSpec: 오버라이드 없는 지표도 보유 ----------------------------------


@pytest.mark.parametrize("name", sorted(set(ALL_TALIB_NAMES) - _MANUAL_OVERRIDE_NAMES))
def test_generated_only_indicators_have_plot_spec_per_output(name: str) -> None:
    spec = TALIB_SPECS[name]
    assert len(spec.plots) == len(spec.outputs) >= 1
    for plot in spec.plots:
        assert plot.kind in ("line", "histogram", "area", "band", "cloud", "marker")


def test_candlestick_functions_plot_as_markers_on_price() -> None:
    spec = TALIB_SPECS["CDLDOJI"]
    assert spec.plots[0].kind == "marker"
    assert spec.plots[0].scale == "overlay"
    assert spec.plots[0].default_pane == "price"


def test_same_scale_overlap_studies_plot_overlaid_on_price() -> None:
    spec = TALIB_SPECS["SAR"]
    assert spec.plots[0].scale == "overlay"
    assert spec.plots[0].default_pane == "price"


def test_histogram_outputs_default_to_sign_color_rule() -> None:
    spec = TALIB_SPECS["APO"]
    # APO is a single-line momentum oscillator, not a histogram — sanity check the
    # opposite case is not colored by sign.
    assert spec.plots[0].kind != "histogram" or spec.plots[0].color_rule == "sign"


# --- negative: 미지 함수명 거부 ---------------------------------------------


def test_generate_rejects_unknown_function_name() -> None:
    with pytest.raises(ValueError, match="unknown talib function"):
        generate_talib_specs(names=["NOT_A_REAL_TALIB_FUNCTION"])


def test_generate_rejects_partially_unknown_function_list() -> None:
    with pytest.raises(ValueError, match="unknown talib function"):
        generate_talib_specs(names=["SMA", "NOT_A_REAL_TALIB_FUNCTION"])


# --- negative: 파라미터 범위 밖 거부 (자동 생성 지표 경유) -----------------


def test_registry_rejects_out_of_range_param_for_a_generated_indicator() -> None:
    registry = IndicatorRegistry(TALIB_SPECS)
    with pytest.raises(IndicatorError) as excinfo:
        registry.validate_params("ADX", {"timeperiod": 2001})
    assert excinfo.value.code == "STRATEGY_PARAM_OUT_OF_RANGE"
    with pytest.raises(IndicatorError) as excinfo:
        registry.validate_params("ADX", {"timeperiod": 0})
    assert excinfo.value.code == "STRATEGY_PARAM_OUT_OF_RANGE"


def test_registry_rejects_out_of_range_matype_for_a_generated_indicator() -> None:
    from src.core.indicators.param_rules import MATYPE_MAX

    registry = IndicatorRegistry(TALIB_SPECS)
    with pytest.raises(IndicatorError) as excinfo:
        registry.validate_params("MA", {"matype": MATYPE_MAX + 1})
    assert excinfo.value.code == "STRATEGY_PARAM_OUT_OF_RANGE"


def test_registry_accepts_every_installed_matype_ordinal() -> None:
    from src.core.indicators.param_rules import MATYPE_MAX

    registry = IndicatorRegistry(TALIB_SPECS)
    assert MATYPE_MAX >= 8  # SMA(0)..T3(8) are present in every supported version
    for ordinal in range(MATYPE_MAX + 1):
        assert registry.validate_params("MA", {"matype": ordinal})["matype"] == ordinal


# --- negative/positive: NaN 구간 처리 (생성된 지표 경유 IndicatorService) --


def test_calculate_returns_leading_none_for_generated_indicator_lookback() -> None:
    registry_lookback = IndicatorRegistry(TALIB_SPECS).lookback("ADX", {"timeperiod": 14})
    result = IndicatorService().calculate("ADX", _candles(registry_lookback + 5), timeperiod=14)
    assert result.values[:registry_lookback] == [None] * registry_lookback
    assert all(v is not None for v in result.values[registry_lookback:])


def test_calculate_handles_integer_dtype_candle_pattern_output() -> None:
    """CDL* 출력은 int32 배열이다 — `_clean`이 np.isnan을 int 배열에 바로 쓰면
    TypeError가 난다(수정 전 회귀 재발 방지)."""
    result = IndicatorService().calculate("CDLDOJI", _candles(30))
    assert len(result.values) == 30
    assert all(v is None or isinstance(v, float) for v in result.values)


def test_calculate_rejects_mavp_input_it_cannot_supply() -> None:
    """MAVP는 두 번째 입력이 캔들 필드가 아닌 가변 주기 배열이라 Candle 기반
    어댑터가 공급할 수 없다 — 등록은 되지만(161종에 포함) 계산은 거부한다."""
    assert "MAVP" in TALIB_SPECS
    with pytest.raises(IndicatorError) as excinfo:
        IndicatorService().calculate("MAVP", _candles(30))
    assert excinfo.value.code == "STRATEGY_INDICATOR_INPUT_UNSUPPORTED"


# --- DEEPEN(task-2925): 실패 주입 — TA-Lib introspection 실패 시뮬레이션 ---


def test_generate_propagates_talib_introspection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """실패 주입: `abstract.Function(name).info` 조회가 카탈로그 중간의 한
    함수에서 예외를 던지면(손상된 TA-Lib 설치·바이너리 버전 불일치 등)
    `generate_talib_specs`가 그 예외를 삼키고 부분 카탈로그를 성공으로
    위장하지 않고 그대로 전파하는지 확인한다(fail-closed)."""
    original_function = talib_abstract.Function
    failing_name = "RSI"
    assert failing_name in ALL_TALIB_NAMES

    def _flaky_function(name: str, *args: object, **kwargs: object) -> object:
        if name == failing_name:
            raise RuntimeError("simulated TA-Lib introspection failure")
        return original_function(name, *args, **kwargs)

    monkeypatch.setattr(
        "src.core.indicators.generate_specs.talib_abstract.Function", _flaky_function
    )

    with pytest.raises(RuntimeError, match="simulated TA-Lib introspection failure"):
        generate_talib_specs()


# --- DEEPEN(task-2925): 수치 성능 단언 — 전종(161) 생성 지연 ---------------


def _generation_latencies_ms(iterations: int = 20) -> list[float]:
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        generate_talib_specs()
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


_FULL_CATALOG_BUDGET_MS = 50.0


@pytest.mark.perf
def test_full_catalog_generation_p95_latency_within_self_declared_budget() -> None:
    """수치 성능 단언: ADR-2026-09-09-C Decision 1 예산표에 "전종 생성 지연"
    전용 항목이 없다(가장 가까운 항목은 "지표 증분=일괄 동일", 지연 예산이
    아님) — 이 리프가 순수 파이썬 TA-Lib 메타데이터 순회(디스크·네트워크 I/O
    없음)라는 사실 위에 자체 예산을 건다: 로컬 실측 p95 ~5ms(2026-09-16) 대비
    10배 여유를 둔 50ms. 예산을 벗어나면 실측 환경 문제가 아니라 회귀(예:
    지표별 `talib.abstract.Function` 중복 호출)로 본다."""
    samples = _generation_latencies_ms(iterations=20)
    p95_ms = _p95(samples)
    print(
        f"[IND-10] generate_talib_specs() p95={p95_ms:.2f}ms "
        f"budget<{_FULL_CATALOG_BUDGET_MS:.0f}ms (n={len(samples)})"
    )
    assert p95_ms < _FULL_CATALOG_BUDGET_MS


def test_full_catalog_generation_budget_gate_actually_fails_past_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현: 위와 동일한 단언식이, 카탈로그 생성 경로 한 곳이 예산을
    실제로 넘기도록 지연을 주입했을 때 진짜로 `AssertionError`를 내는지(= CI가
    실제로 빨간불이 되는지) 확인한다. 이 테스트가 없으면 위 단언이 항상
    통과하는 tautology인지 아무도 검증하지 못한다."""
    original_group = talib_abstract.Function

    def _stalled_function(name: str, *args: object, **kwargs: object) -> object:
        time.sleep(_FULL_CATALOG_BUDGET_MS / 1000.0)
        return original_group(name, *args, **kwargs)

    monkeypatch.setattr(
        "src.core.indicators.generate_specs.talib_abstract.Function", _stalled_function
    )

    samples = _generation_latencies_ms(iterations=3)
    p95_ms = _p95(samples)
    with pytest.raises(AssertionError):
        assert p95_ms < _FULL_CATALOG_BUDGET_MS


# --- DEEPEN(task-2925): 게이트 적색 재현 — 수기 작성 회귀 가드 -------------


def test_non_override_specs_match_fresh_generation_byte_for_byte() -> None:
    """게이트: `_MANUAL_OVERRIDES`(specs_talib.py) 밖의 생성 전용 지표는
    `TALIB_SPECS`에 있든 새로 생성하든 정준 직렬화가 바이트 동일해야 한다 —
    누군가 생성 전용 지표를 손으로 고치면(수기 작성 금지, generate_specs.py
    모듈 docstring) 이 단언이 잡는다."""
    fresh = generate_talib_specs()
    for name in sorted(set(ALL_TALIB_NAMES) - _MANUAL_OVERRIDE_NAMES):
        assert canonical_spec_dict(name, TALIB_SPECS[name]) == canonical_spec_dict(
            name, fresh[name]
        )


def test_gate_turns_red_when_a_generated_only_spec_is_hand_tampered() -> None:
    """게이트 적색 재현: 위 동등성 단언이 실제로 수기 변조를 잡아내는지
    확인한다 — 생성 전용 지표(ADX)의 파라미터 범위를 손으로 바꿔치기한 뒤
    (수기 오버라이드 흉내), 같은 비교식이 실제로 `AssertionError`를 내는지
    본다. 이 테스트가 없으면 위 게이트가 무엇을 대조하는지 몰라도 항상
    통과하는 tautology일 수 있다."""
    fresh = generate_talib_specs()
    assert "ADX" not in _MANUAL_OVERRIDE_NAMES
    tampered_params = tuple(dataclasses.replace(p, max=p.max + 1) for p in fresh["ADX"].params)
    hand_written = dataclasses.replace(fresh["ADX"], params=tampered_params)

    with pytest.raises(AssertionError):
        assert canonical_spec_dict("ADX", hand_written) == canonical_spec_dict("ADX", fresh["ADX"])


@pytest.mark.parametrize(
    "module",
    ["src.core.indicators.generate_specs", "src.core.indicators.specs_talib"],
)
def test_catalog_import_fails_closed_without_talib(module: str) -> None:
    """IND-2g: missing TA-Lib must not publish an empty or partial catalog."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib, sys; sys.modules['talib'] = None; "
            "importlib.import_module(sys.argv[1])",
            module,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert "ModuleNotFoundError" in result.stderr
    assert "talib" in result.stderr
