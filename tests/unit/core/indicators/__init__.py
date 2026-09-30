"""DEEPEN: tests/unit/core/indicators/__init__.py

Negative / failure-injection / performance tests for the indicators package.

DoD checklist (task-9981):
- [x] negative test 3건 이상 추가 (불변식 위반 입력을 명시적으로 거부하는 케이스)
- [x] 실패주입 케이스 1건 이상 추가 (monkeypatch로 의존성 예외 유발 등)
- [x] `python -m pytest tests/unit/core/indicators/__init__.py -q` 통과
- [x] docs/design/INVARIANTS.md 위반 없음
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from src.core.indicators.registry import (
    ChainNode,
    ColumnSource,
    IndicatorError,
    IndicatorRegistry,
    NodeSource,
    resolve_chain,
)
from src.core.indicators.spec import IndicatorSpec, ParamSpec, PlotSpec


def _spec(name: str, params: tuple[ParamSpec, ...] = ()) -> IndicatorSpec:
    return IndicatorSpec(
        name=name,
        inputs=("close",),
        params=params,
        outputs=("value",),
        lookback=lambda p: 0,
        plots=(PlotSpec(kind="line", scale="own", default_pane="separate"),),
    )


class TestRegistryNegative:
    """Negative tests -- IndicatorRegistry가 잘못된 입력을 명시적으로 거부하는지 검증."""

    def test_get_unknown_raises(self) -> None:
        """존재하지 않는 지표명으로 get() 호출 시 IndicatorError(STRATEGY_INDICATOR_UNKNOWN)."""
        reg = IndicatorRegistry(specs={})
        with pytest.raises(IndicatorError) as exc_info:
            reg.get("UNKNOWN_IND")
        assert exc_info.value.code == "STRATEGY_INDICATOR_UNKNOWN"

    def test_validate_unknown_param_raises(self) -> None:
        """정의되지 않은 파라미터를 전달하면 IndicatorError(STRATEGY_PARAM_UNKNOWN)."""
        spec = _spec("STRICT_PARAMS", (ParamSpec(name="period", min=1, max=50, default=5),))
        reg = IndicatorRegistry(specs={"STRICT_PARAMS": spec})
        with pytest.raises(IndicatorError) as exc_info:
            reg.validate_params("STRICT_PARAMS", {"bogus": 1})
        assert exc_info.value.code == "STRATEGY_PARAM_UNKNOWN"

    def test_validate_param_out_of_range_raises(self) -> None:
        """파라미터가 ParamSpec min/max 범위를 벗어나면 STRATEGY_PARAM_OUT_OF_RANGE."""
        spec = _spec("RANGE_TEST", (ParamSpec(name="timeperiod", min=2, max=100, default=10),))
        reg = IndicatorRegistry(specs={"RANGE_TEST": spec})
        with pytest.raises(IndicatorError) as exc_info:
            reg.validate_params("RANGE_TEST", {"timeperiod": 0})
        assert exc_info.value.code == "STRATEGY_PARAM_OUT_OF_RANGE"
        with pytest.raises(IndicatorError) as exc_info:
            reg.validate_params("RANGE_TEST", {"timeperiod": 101})
        assert exc_info.value.code == "STRATEGY_PARAM_OUT_OF_RANGE"

    def test_validate_non_int_param_raises(self) -> None:
        """bool/float 등 int가 아닌 값은 STRATEGY_PARAM_OUT_OF_RANGE로 거부된다(bool은 int의
        서브클래스이므로 별도 isinstance(value, bool) 체크가 필요 — 우회 경로 회귀 방지)."""
        spec = _spec("TYPE_TEST", (ParamSpec(name="timeperiod", min=1, max=200, default=10),))
        reg = IndicatorRegistry(specs={"TYPE_TEST": spec})
        with pytest.raises(IndicatorError) as exc_info:
            reg.validate_params("TYPE_TEST", {"timeperiod": True})
        assert exc_info.value.code == "STRATEGY_PARAM_OUT_OF_RANGE"
        with pytest.raises(IndicatorError) as exc_info:
            reg.validate_params("TYPE_TEST", {"timeperiod": 1.5})
        assert exc_info.value.code == "STRATEGY_PARAM_OUT_OF_RANGE"

    def test_lookback_unknown_indicator_raises(self) -> None:
        """lookback()도 get()을 경유하므로 미지 지표명에 동일하게 fail-closed."""
        reg = IndicatorRegistry(specs={})
        with pytest.raises(IndicatorError) as exc_info:
            reg.lookback("NOPE", {})
        assert exc_info.value.code == "STRATEGY_INDICATOR_UNKNOWN"


class TestIndicatorSpecNegative:
    """Negative tests -- IndicatorSpec/PlotSpec __post_init__ 불변식."""

    def test_plots_count_mismatch_raises(self) -> None:
        """outputs 개수와 plots 개수가 다르면 ValueError."""
        with pytest.raises(ValueError, match="PlotSpec count"):
            IndicatorSpec(
                name="BAD",
                inputs=("close",),
                params=(),
                outputs=("a", "b"),
                lookback=lambda p: 0,
                plots=(PlotSpec(kind="line", scale="own", default_pane="separate"),),
            )

    def test_fill_between_not_in_outputs_raises(self) -> None:
        """fill_between이 outputs에 없는 이름을 가리키면 ValueError."""
        with pytest.raises(ValueError, match="fill_between"):
            IndicatorSpec(
                name="BAD2",
                inputs=("close",),
                params=(),
                outputs=("only",),
                lookback=lambda p: 0,
                plots=(
                    PlotSpec(
                        kind="band",
                        scale="own",
                        default_pane="separate",
                        fill_between="missing",
                    ),
                ),
            )

    def test_plot_spec_invalid_kind_raises(self) -> None:
        """PlotSpec.kind가 허용 리터럴 밖이면 ValueError."""
        with pytest.raises(ValueError, match="unknown PlotSpec.kind"):
            PlotSpec(kind="bogus", scale="own", default_pane="separate")  # type: ignore[arg-type]


class TestChainNegative:
    """Negative tests -- IND-16 resolve_chain의 fail-closed 경로."""

    def test_resolve_chain_self_cycle_raises(self) -> None:
        """자기 자신을 입력으로 참조하면 INDICATOR_CHAIN_CYCLE."""
        spec = _spec("A")
        reg = IndicatorRegistry(specs={"A": spec})
        graph = {
            "n1": ChainNode(
                name="A", params={}, inputs={"close": NodeSource(node="n1", output="value")}
            )
        }
        with pytest.raises(IndicatorError) as exc_info:
            resolve_chain(graph, "n1", reg)
        assert exc_info.value.code == "INDICATOR_CHAIN_CYCLE"

    def test_resolve_chain_unknown_node_raises(self) -> None:
        """존재하지 않는 노드를 참조하면 INDICATOR_INPUT_INVALID."""
        spec = _spec("A")
        reg = IndicatorRegistry(specs={"A": spec})
        graph = {
            "n1": ChainNode(
                name="A",
                params={},
                inputs={"close": NodeSource(node="missing", output="value")},
            )
        }
        with pytest.raises(IndicatorError) as exc_info:
            resolve_chain(graph, "n1", reg)
        assert exc_info.value.code == "INDICATOR_INPUT_INVALID"

    def test_resolve_chain_too_deep_raises(self) -> None:
        """MAX_CHAIN_DEPTH(8)를 초과하는 선형 체인은 INDICATOR_CHAIN_TOO_DEEP."""
        spec = _spec("A")
        reg = IndicatorRegistry(specs={"A": spec})
        depth = 12
        graph = {}
        for i in range(depth):
            node_id = f"n{i}"
            if i == 0:
                inputs = {"close": ColumnSource(column="close")}
            else:
                inputs = {"close": NodeSource(node=f"n{i - 1}", output="value")}
            graph[node_id] = ChainNode(name="A", params={}, inputs=inputs)
        with pytest.raises(IndicatorError) as exc_info:
            resolve_chain(graph, f"n{depth - 1}", reg)
        assert exc_info.value.code == "INDICATOR_CHAIN_TOO_DEEP"

    def test_resolve_chain_input_name_mismatch_raises(self) -> None:
        """node.inputs 키가 spec.inputs와 다르면 INDICATOR_INPUT_INVALID."""
        spec = _spec("A")
        reg = IndicatorRegistry(specs={"A": spec})
        graph = {
            "n1": ChainNode(
                name="A", params={}, inputs={"wrong_name": ColumnSource(column="close")}
            )
        }
        with pytest.raises(IndicatorError) as exc_info:
            resolve_chain(graph, "n1", reg)
        assert exc_info.value.code == "INDICATOR_INPUT_INVALID"


class TestGenerateSpecsNegative:
    """Negative tests -- generate_talib_specs의 실패 조건."""

    def test_generate_unknown_name_raises(self) -> None:
        """존재하지 않는 TA-Lib 함수명으로 generate_talib_specs() 호출 시 ValueError."""
        from src.core.indicators.generate_specs import generate_talib_specs

        with pytest.raises(ValueError, match="unknown talib function"):
            generate_talib_specs(names=["NONEXISTENT_FUNC_12345"])

    def test_generate_empty_list_returns_empty(self) -> None:
        """빈 리스트를 전달하면 빈 딕셔너리를 반환한다(unknown-name 오류와 구분되는 정상 경로)."""
        from src.core.indicators.generate_specs import generate_talib_specs

        result = generate_talib_specs(names=[])
        assert result == {}


class TestLookbackRequiredBarsNegative:
    """Negative tests -- required_bars의 @timeframe 누락 거부."""

    def test_required_bars_missing_timeframe_raises(self) -> None:
        """@timeframe 접미사가 없는 키는 명시적으로 거부된다(암묵적 기본 timeframe 없음)."""
        from src.core.indicators.lookback import required_bars

        reg = IndicatorRegistry(specs={})
        with pytest.raises(ValueError, match="no @timeframe"):
            required_bars(["RSI_timeperiod14"], reg)


class TestFailureInjection:
    """실패주입 테스트 -- monkeypatch로 의존성 예외를 유발."""

    def test_generate_specs_monkeypatch_empty_metadata_cache(self) -> None:
        """TA-Lib 메타데이터 캐시가 비어 있으면(예: 초기화 실패) 알려진 함수명도 ValueError로
        fail-closed된다 -- 조용히 빈 스펙을 반환하지 않는다."""
        from src.core.indicators.generate_specs import generate_talib_specs

        with patch(
            "src.core.indicators.generate_specs._get_talib_metadata_cache",
            return_value={},
        ):
            with pytest.raises(ValueError, match="unknown talib function"):
                generate_talib_specs(names=["SMA"])

    def test_registry_get_monkeypatch_missing_spec(self) -> None:
        """레지스트리 내부 _specs가 예기치 않게 비워지면(의존성 손상 시뮬레이션) get()이
        IndicatorError로 fail-closed된다."""
        spec = _spec("INJECT_TEST")
        reg = IndicatorRegistry(specs={"INJECT_TEST": spec})
        assert reg.get("INJECT_TEST") is spec
        with patch.object(reg, "_specs", {}):
            with pytest.raises(IndicatorError) as exc_info:
                reg.get("INJECT_TEST")
            assert exc_info.value.code == "STRATEGY_INDICATOR_UNKNOWN"


class TestPerformance:
    """성능 테스트 -- 지표 조회/검증이 예산 내 완료되는지."""

    def test_registry_lookup_budget(self) -> None:
        """registry.get()이 1000회 조회 기준 1초 이내에 완료되어야 한다(1회당 1ms 예산)."""
        spec = _spec("PERF_TEST")
        reg = IndicatorRegistry(specs={"PERF_TEST": spec})
        start = time.monotonic()
        for _ in range(1000):
            reg.get("PERF_TEST")
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"1000회 registry.get()이 {elapsed:.2f}s 걸림 (예산: 1s)"

    def test_registry_hash_budget(self) -> None:
        """registry_hash()가 100개 스펙 기준 1초 이내에 완료되어야 한다."""
        specs = {f"IND{i}": _spec(f"IND{i}") for i in range(100)}
        reg = IndicatorRegistry(specs=specs)
        start = time.monotonic()
        reg.registry_hash()
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"registry_hash()가 {elapsed:.2f}s 걸림 (예산: 1s)"
