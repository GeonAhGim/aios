"""IND-11 — `adapters/pandas_ta_bridge.py` 계약 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 IND-11,
ADR-2026-09-09-A, docs/design/INDICATOR_OSS_EVAL.md §5·§6.

DoD: 순증분 종수 보고, 중복 0(설치된 TA-Lib 전 함수명과 이름 기반 대조), `pip show
tulipy` not found(oracle extra 미설치), 후보 지표(DPO/MASSI/COPPOCK) 3자 교차검증
(IND-7g 방식 — 여기서는 (1) pandas-ta-classic 직접 호출 (2) 공개된 산식의
독립 numpy/pandas 재구현 (3) MASSI는 추가로 TA-Lib EMA를 서브스텝 제3
오라클로 사용).

D2: negative >=3, failure-injection 1, 수치 성능 단언 1, 게이트 적색 재현 1.

버전 유연성: 후보 3종은 TA-Lib 0.6.x(파이썬 래퍼 0.8.x)부터 TA-Lib 자체가
제공하므로 그 버전에서는 `SUPERSEDED_BY_TALIB`로 분류되어 기본 서비스가 거부한다.
산식 교차검증은 설치 버전과 무관하게 성립해야 하므로 `CANDIDATE_REGISTRY`로 만든
서비스(`_candidate_service()`)로 계산한다 — 스킵 없이 두 버전 모두에서 같은 단언.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import talib

from src.core.indicators.adapters import pandas_ta_bridge as bridge
from src.core.indicators.registry import IndicatorError, IndicatorRegistry
from src.core.indicators.specs_talib import TALIB_SPECS
from src.data.models.market_data import Candle
from tests.conftest import PerfBudget


def _candidate_service() -> bridge.PandasTaBridgeService:
    """Formula checks must run on every candidate regardless of which TA-Lib
    version is installed (the default service refuses superseded names)."""
    return bridge.PandasTaBridgeService(registry=bridge.CANDIDATE_REGISTRY)


def _candles_random_walk(n: int, *, seed: int = 7, base: float = 100.0) -> list[Candle]:
    rng = np.random.default_rng(seed)
    close = base + np.cumsum(rng.normal(size=n))
    close = np.maximum(close, 1.0)
    spread = np.abs(rng.normal(size=n)) + 0.1
    high = close + spread
    low = np.maximum(close - spread, 0.01)
    now = datetime.now(timezone.utc)
    out = []
    for i in range(n):
        out.append(
            Candle(
                symbol="BTC/USDT",
                exchange="bitget",
                timeframe="1h",
                open=Decimal(str(close[i])),
                high=Decimal(str(high[i])),
                low=Decimal(str(low[i])),
                close=Decimal(str(close[i])),
                volume=Decimal("1000"),
                open_time=now + timedelta(hours=i),
                close_time=now + timedelta(hours=i + 1),
            )
        )
    return out


# --- 순증분 카탈로그 · 중복 0 ------------------------------------------------


def test_every_installed_talib_name_is_detected_as_overlap() -> None:
    """이름 기반 중복 판정이 설치된 TA-Lib 전 함수를 실제로 잡아내는지 직접 확인
    (역방향: 판정이 항상 False를 반환하는 회귀를 막는다)."""
    for name in TALIB_SPECS:
        assert bridge.is_talib_overlap(name.lower()), name


def test_net_incremental_report_is_consistent_with_catalog_sets() -> None:
    report = bridge.report_net_incremental_count()
    assert report["total"] == len(bridge.ALL_PANDAS_TA_NAMES)
    assert report["net_incremental"] == len(bridge.NET_INCREMENTAL_NAMES)
    assert report["overlap"] == report["total"] - report["net_incremental"]
    assert report["net_incremental"] > 0
    assert report["overlap"] > 0


def test_registered_indicators_have_zero_talib_overlap() -> None:
    for name in bridge.REGISTERED_INDICATORS:
        assert name in bridge.NET_INCREMENTAL_NAMES
        assert not bridge.is_talib_overlap(name)


def test_candidates_split_exactly_into_registered_and_superseded() -> None:
    """설치된 TA-Lib이 어떤 버전이든: 후보 = 등록 ∪ TA-Lib 대체, 교집합 0,
    대체된 이름은 전부 실제 TA-Lib 함수명(대문자)이다."""
    registered = set(bridge.REGISTERED_INDICATORS)
    superseded = set(bridge.SUPERSEDED_BY_TALIB)
    assert registered | superseded == set(bridge.CANDIDATE_INDICATORS)
    assert registered & superseded == set()
    for name in superseded:
        assert bridge.is_talib_overlap(name), name
    assert set(bridge.PANDAS_TA_SPECS) == registered


def test_select_registered_drops_candidates_the_installed_talib_provides() -> None:
    """순수 함수 단위 검증 — TA-Lib을 재설치하지 않고 두 버전의 카탈로그를 흉내낸다:
    0.4.x(세 후보 모두 순증분) → 3종 등록 / 0.6.x(DPO·MASSI·COPPOCK 자체 제공) →
    0종 등록·3종 대체."""
    all_net = frozenset(bridge.CANDIDATE_INDICATORS)
    registered, superseded = bridge.select_registered(bridge.CANDIDATE_SPECS, all_net)
    assert set(registered) == set(bridge.CANDIDATE_INDICATORS) and superseded == ()

    registered, superseded = bridge.select_registered(bridge.CANDIDATE_SPECS, frozenset())
    assert registered == {} and superseded == bridge.CANDIDATE_INDICATORS

    registered, superseded = bridge.select_registered(
        bridge.CANDIDATE_SPECS, all_net - {"dpo"}
    )
    assert set(registered) == set(bridge.CANDIDATE_INDICATORS) - {"dpo"}
    assert superseded == ("dpo",)


def test_alias_targets_are_all_real_talib_names() -> None:
    """`ALIAS_TO_TALIB`가 실재하지 않는 TA-Lib 이름을 가리키는 오타 회귀 방지."""
    for pandas_ta_name, talib_name in bridge.ALIAS_TO_TALIB.items():
        assert talib_name in TALIB_SPECS, (pandas_ta_name, talib_name)


# --- oracle extra(tulipy, LGPL-3.0) 미설치 -----------------------------------


def test_tulipy_oracle_extra_is_not_installed() -> None:
    """§5: `pandas-ta-classic`의 `oracle` extra는 `tulipy`(LGPL-3.0)를 끌어온다
    — AIOS는 이 extra를 설치하지 않는다. `pip show`가 이 환경에서 실제로
    tulipy를 찾지 못함을 단언한다(문서상 주장이 아니라 실측)."""
    result = subprocess.run(
        [sys.executable, "-m", "pip", "show", "tulipy"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "not found" in (result.stdout + result.stderr).lower()


# --- negative tests (>=3) ----------------------------------------------------


def test_unknown_indicator_raises() -> None:
    with pytest.raises(IndicatorError) as exc:
        bridge.PandasTaBridgeService().calculate("nope", _candles_random_walk(50))
    assert exc.value.code == "STRATEGY_INDICATOR_UNKNOWN"


def test_superseded_candidate_is_refused_not_computed_twice() -> None:
    """게이트 적색 재현(TA-Lib 0.6.x): 설치된 TA-Lib이 DPO를 제공하는 상황을
    `select_registered`로 재현하면 기본 서비스는 DPO를 두 번째 계산 경로로 돌리지
    않고 명시적 코드로 거부해야 한다(ADR-2026-09-06-F). 다른 후보는 그대로 계산."""
    registered, superseded = bridge.select_registered(
        bridge.CANDIDATE_SPECS, frozenset(bridge.CANDIDATE_INDICATORS) - {"dpo"}
    )
    assert superseded == ("dpo",)
    service = bridge.PandasTaBridgeService(registry=IndicatorRegistry(registered))
    with pytest.raises(IndicatorError) as exc:
        service.calculate("dpo", _candles_random_walk(50))
    assert exc.value.code == "PANDAS_TA_SUPERSEDED_BY_TALIB"
    assert service.calculate("massi", _candles_random_walk(80)).values


def test_unknown_param_raises() -> None:
    with pytest.raises(IndicatorError) as exc:
        _candidate_service().calculate(
            "dpo", _candles_random_walk(50), not_a_real_param=3
        )
    assert exc.value.code == "STRATEGY_PARAM_UNKNOWN"


def test_out_of_range_param_raises() -> None:
    with pytest.raises(IndicatorError) as exc:
        _candidate_service().calculate("dpo", _candles_random_walk(50), length=1)
    assert exc.value.code == "STRATEGY_PARAM_OUT_OF_RANGE"


def test_insufficient_data_returns_message_not_exception() -> None:
    result = _candidate_service().calculate("coppock", _candles_random_walk(5))
    assert result.values == []
    assert result.message is not None


# --- failure injection --------------------------------------------------------


def test_calculate_raises_when_library_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """pandas-ta-classic이 `None`을 반환하는 경로(입력 검증 실패 등)를 조용히
    통과시키지 않고 fail-closed로 거부하는지 — `pta.dpo`를 직접 패치해 주입."""
    monkeypatch.setattr(bridge.pta, "dpo", lambda *a, **k: None)
    with pytest.raises(IndicatorError) as exc:
        _candidate_service().calculate("dpo", _candles_random_walk(50))
    assert exc.value.code == "STRATEGY_INDICATOR_COMPUTE_FAILED"


# --- gate-red reproduction: lookback bound must not understate real need ----


@pytest.mark.parametrize("name", bridge.CANDIDATE_INDICATORS)
def test_lookback_bound_is_sufficient_for_a_non_nan_last_value(name: str) -> None:
    """게이트 적색 재현: `lookback()`이 실제 필요량보다 작으면 최소 바 수만
    공급했을 때 마지막 값이 NaN(불안정 구간)인데도 `values`에 유효값처럼
    노출된다 — 이 테스트는 `_dpo_lookback`/`_massi_lookback`/`_coppock_lookback`을
    고의로 줄이면(예: `- 1`) 바로 적색이 된다(로컬 검증 완료)."""
    spec = bridge.CANDIDATE_SPECS[name]
    params = {p.name: p.default for p in spec.params}
    min_required = spec.lookback(params) + 1
    candles = _candles_random_walk(min_required, seed=11)
    result = _candidate_service().calculate(name, candles, **params)
    assert result.values, name
    assert result.values[-1] is not None, name


# --- IND-7g 방식 3자 교차검증 -------------------------------------------------


def test_dpo_matches_independent_reimplementation() -> None:
    candles = _candles_random_walk(300, seed=1)
    length = 20
    result = _candidate_service().calculate("dpo", candles, length=length)

    close = pd.Series([float(c.close) for c in candles])
    independent = close - close.rolling(length).mean().shift(length // 2 + 1)

    got = np.array([np.nan if v is None else v for v in result.values])
    finite = ~np.isnan(independent.to_numpy())
    np.testing.assert_allclose(got[finite], independent.to_numpy()[finite], atol=1e-9)
    assert np.array_equal(np.isnan(got), np.isnan(independent.to_numpy()))


def test_massi_matches_talib_ema_oracle_and_independent_rolling_sum() -> None:
    """MASSI는 TA-Lib EMA(제3 오라클)로 서브스텝을 검증하고, 바깥 rolling-sum
    래퍼는 독립 재구현으로 검증한다 — pandas-ta-classic 직접 호출(1) + TA-Lib
    EMA(2) + 우리 rolling-sum(3), IND-7g와 같은 3자 구조."""
    candles = _candles_random_walk(300, seed=2)
    fast, slow = 9, 25
    result = _candidate_service().calculate("massi", candles, fast=fast, slow=slow)

    high = np.array([float(c.high) for c in candles])
    low = np.array([float(c.low) for c in candles])
    hl_range = high - low
    ema1 = talib.EMA(hl_range, fast)
    ema2 = talib.EMA(ema1, fast)
    ratio = pd.Series(ema1 / ema2)
    independent = ratio.rolling(slow, min_periods=slow).sum()

    got = np.array([np.nan if v is None else v for v in result.values])
    finite = ~np.isnan(independent.to_numpy())
    np.testing.assert_allclose(got[finite], independent.to_numpy()[finite], atol=1e-8)


def test_coppock_matches_independent_reimplementation() -> None:
    candles = _candles_random_walk(300, seed=3)
    length, fast, slow = 10, 11, 14
    result = _candidate_service().calculate(
        "coppock", candles, length=length, fast=fast, slow=slow
    )

    close = pd.Series([float(c.close) for c in candles])
    roc_fast = 100 * (close / close.shift(fast) - 1)
    roc_slow = 100 * (close / close.shift(slow) - 1)
    total = (roc_fast + roc_slow).to_numpy()
    weights = np.arange(1, length + 1, dtype=np.float64)
    independent = np.full(len(total), np.nan)
    for i in range(length - 1, len(total)):
        window = total[i - length + 1 : i + 1]
        if np.any(np.isnan(window)):
            continue
        independent[i] = np.dot(window, weights) / weights.sum()

    got = np.array([np.nan if v is None else v for v in result.values])
    finite = ~np.isnan(independent)
    np.testing.assert_allclose(got[finite], independent[finite], atol=1e-8)


# --- causal / lookahead-off ---------------------------------------------------


def test_dpo_is_causal_changing_future_candles_does_not_change_past_output() -> None:
    """`causal=True` 계약: 미래 봉 값을 바꿔도 과거 인덱스의 출력이 달라지면
    안 된다 — `dpo(..., centered=False)`가 실제로 look-ahead를 끄고 있는지의
    직접 증거(라이브러리 기본값 `centered=True`는 미래를 참조한다)."""
    base = _candles_random_walk(120, seed=5)
    service = _candidate_service()
    result_a = service.calculate("dpo", base, length=20)

    mutated = list(base)
    last = mutated[-1]
    mutated[-1] = last.model_copy(update={"close": last.close + Decimal("500")})
    result_b = service.calculate("dpo", mutated, length=20)

    assert result_a.values[:-5] == result_b.values[:-5]


# --- 수치 성능 단언 ------------------------------------------------------------


@pytest.mark.perf
def test_registered_indicators_compute_within_budget(perf_budget: PerfBudget) -> None:
    """예산: 3종 x 2000봉 = 전부 순수 numpy/pandas 연산(네트워크·DB 없음) —
    ADR-2026-09-09-C Decision 1에 이 리프 전용 항목은 없어(가장 가까운 항목은
    "지표 증분=일괄 동일" 계열) 이 leaf가 자체 선언한 예산을 건다: 로컬 실측
    (3종 x 2000봉) p95 ~40ms 대비 10배 여유인 400ms.

    task-7672: raw `time.perf_counter()`(wall-clock)는 공유 CI 호스트에서 다른
    프로세스에 코어를 뺏기면 이 프로세스가 실제로 쓴 CPU 시간과 무관하게 값이
    치솟는다(실측: 같은 호출이 단독 실행 시 cpu=15.6ms/wall=17.2ms인데, 호스트가
    바쁠 때 wall만 6.6s까지 뛰었다 — esc-ci-pytest 3300s 적색의 재현 조건).
    `perf_budget`(tests/conftest.py PerfBudget)의 `time.process_time()` 기반
    측정으로 바꿔 예산 판정에서 호스트 경합을 제외한다 — 예산 수치(0.4s)는
    그대로 둔다."""
    candles = _candles_random_walk(2000, seed=9)
    service = _candidate_service()

    def run() -> None:
        for name in bridge.CANDIDATE_INDICATORS:
            spec = bridge.CANDIDATE_SPECS[name]
            params = {p.name: p.default for p in spec.params}
            service.calculate(name, candles, **params)

    perf_budget.assert_within(run, budget_ms=400.0, label="registered_indicators_batch")


def test_dependency_declared_without_oracle_extra() -> None:
    pyproject = Path(__file__).resolve().parents[4] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert "pandas-ta-classic" in text
    assert "[oracle]" not in text
    assert '"tulipy' not in text
