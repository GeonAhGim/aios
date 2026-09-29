"""IND-7g — `reference/verify_all.py` 3자 교차검증(TA-Lib C ↔ 증분 ↔ 벡터) 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.3 IND-7g
DoD: 3자 일치 지표만 노출(스냅샷 대상), 불일치 지표는 제외되고 그 상세가
`VerificationReport.mismatches`에 남는다(무음 통과 금지).

`test_full_verification_matches_known_state`의 기대 제외 집합은 설치된 TA-Lib에서
직접 도출한다(`_expected_exclusions()`): BBANDS는 `timeperiod=2`(파라미터 최솟값)
경계에서 TA-Lib 0.4.x의 분산 계산(`E[X^2]-E[X]^2` 방식)이 표본 2개일 때 우리 구현
(`E[(X-평균)^2]`, 증분·벡터 엔진이 공유하는 산식)보다 상쇄오차에 취약해 실제로
제외되고, TA-Lib 0.6.x(파이썬 래퍼 0.8.x)는 이 차이가 없어 통과한다. 어느 쪽인지는
TA-Lib C 함수 출력과 닫힌 형식 numpy 산식(엔진 코드 미사용)을 직접 비교해 정하므로
엔진 결과를 되풀이 단언하는 tautology가 아니며, 버전 리터럴도 두지 않는다 —
정확히 IND-7g가 잡아내야 하는 종류의 불일치라 스킵하지 않고 두 버전 모두 단언한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pytest
import talib

from src.core.indicators.engine import incremental, vectorized
from src.core.indicators.reference import verify_all
from src.core.indicators.reference.verify_all import FloatArray
from src.core.indicators.spec import IndicatorSpec
from src.core.indicators.specs_talib import TALIB_SPECS
from tests.conftest import PerfBudget


def test_verifiable_names_require_all_three_implementations() -> None:
    assert verify_all.VERIFIABLE_NAMES == (
        "ATR", "BBANDS", "CCI", "EMA", "MACD", "MFI", "OBV", "RSI", "SMA", "STOCH", "WILLR",
    )  # fmt: skip
    for name in verify_all.VERIFIABLE_NAMES:
        assert name in TALIB_SPECS
        assert name in vectorized._KERNELS
        assert name in incremental._STATES


def _bbands_min_boundary_disagrees_with_closed_form() -> bool:
    """Independent oracle: TA-Lib BBANDS at the minimum window vs. the closed-form
    population-stddev band on the same datasets — no engine code involved."""
    spec = TALIB_SPECS["BBANDS"]
    window = next(p.min for p in spec.params if p.name == "timeperiod")
    worst = 0.0
    for dataset in verify_all.default_datasets():
        close = dataset.columns["close"]
        upper, _middle, _lower = talib.BBANDS(close, timeperiod=window)
        frames = np.lib.stride_tricks.sliding_window_view(close, window)
        mean = frames.mean(axis=1)
        std = np.sqrt(((frames - mean[:, None]) ** 2).mean(axis=1))
        closed_form = np.full(len(close), np.nan)
        closed_form[window - 1 :] = mean + 2.0 * std
        finite = ~np.isnan(upper)
        scale = np.maximum(1.0, np.maximum(np.abs(upper[finite]), np.abs(closed_form[finite])))
        worst = max(worst, float(np.max(np.abs(upper[finite] - closed_form[finite]) / scale)))
    return worst > verify_all.REFERENCE_TOLERANCE


def _expected_exclusions() -> tuple[str, ...]:
    return ("BBANDS",) if _bbands_min_boundary_disagrees_with_closed_form() else ()


def test_full_verification_matches_known_state() -> None:
    report = verify_all.run_verification()
    assert set(report.verified) | set(report.excluded) == set(verify_all.VERIFIABLE_NAMES)
    assert set(report.verified) & set(report.excluded) == set()
    assert report.excluded == _expected_exclusions()
    assert {m.name for m in report.mismatches} == set(report.excluded)
    for mismatch in report.mismatches:
        assert mismatch.worst_rel_error > verify_all.REFERENCE_TOLERANCE


def test_bbands_passes_at_default_params_only_default_boundary_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """기본 파라미터만 놓고 보면 BBANDS도 통과한다 — 실패는 최솟값 경계에서만 난다."""
    monkeypatch.setattr(
        verify_all, "_param_variants", lambda spec: [{p.name: p.default for p in spec.params}]
    )
    assert verify_all.verify_indicator("BBANDS", verify_all.default_datasets()) == []


def test_write_snapshot_persists_reference_vector(tmp_path: Path) -> None:
    dataset = verify_all.default_datasets()[0]
    path = verify_all.write_snapshot("SMA", dataset, vectors_dir=tmp_path)
    assert path == tmp_path / "SMA.json"
    payload = path.read_text(encoding="utf-8")
    assert '"indicator": "SMA"' in payload
    import json

    parsed = json.loads(payload)
    assert parsed["dataset"] == dataset.id
    assert set(parsed["outputs"]) == set(TALIB_SPECS["SMA"].outputs)
    values = parsed["outputs"]["value"]
    assert len(values) == len(dataset.columns["close"])
    assert any(v is None for v in values)
    assert any(isinstance(v, float) for v in values)


def test_run_verification_snapshots_only_verified_names(tmp_path: Path) -> None:
    report = verify_all.run_verification(write_snapshots=True, vectors_dir=tmp_path)
    for name in report.verified:
        assert (tmp_path / f"{name}.json").exists()
    for name in report.excluded:
        assert not (tmp_path / f"{name}.json").exists()


def test_sample_names_is_deterministic_and_caps_at_catalog_size() -> None:
    first = verify_all.sample_names(5)
    second = verify_all.sample_names(5)
    assert first == second
    assert len(first) == 5
    assert set(first) <= set(verify_all.VERIFIABLE_NAMES)
    assert verify_all.sample_names(30) == verify_all.VERIFIABLE_NAMES


# ---------------------------------------------------------------- negative --


def test_injected_engine_drift_is_caught_not_silently_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """벡터 커널을 일부러 어긋나게 만들면 verify_indicator가 놓치지 않고 잡는다."""
    original = vectorized._KERNELS["SMA"]

    def drifted(c: dict[str, FloatArray], p: dict[str, int]) -> tuple[FloatArray, ...]:
        (out,) = original(c, p)
        return (out * (1.0 + 1e-6),)

    monkeypatch.setitem(vectorized._KERNELS, "SMA", drifted)
    mismatches = verify_all.verify_indicator("SMA", verify_all.default_datasets())
    assert mismatches
    assert all(m.name == "SMA" for m in mismatches)


def test_injected_nan_prefix_disagreement_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """엔진 자체는 NaN 접두=lookback을 스스로 검증하므로(`INDICATOR_LOOKBACK_MISMATCH`),
    이 분기는 TA-Lib 직접 호출 쪽 NaN 패턴이 달라지는 경우를 흉내 내 확인한다."""
    original = verify_all._talib_direct

    def broken(
        name: str,
        spec: IndicatorSpec,
        columns: Mapping[str, FloatArray],
        params: dict[str, int],
    ) -> dict[str, FloatArray]:
        result = original(name, spec, columns, params)
        if name != "OBV":
            return result
        tampered = {k: v.copy() for k, v in result.items()}
        for arr in tampered.values():
            arr[0] = np.nan  # OBV는 원래 첫 bar부터 값을 낸다 — 접두 불일치 유도
        return tampered

    monkeypatch.setattr(verify_all, "_talib_direct", broken)
    mismatches = verify_all.verify_indicator("OBV", verify_all.default_datasets())
    assert mismatches
    assert all(m.index == -1 for m in mismatches)


def test_main_exits_nonzero_when_any_indicator_excluded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """제외가 하나라도 있으면 종료 코드 1 — 어떤 TA-Lib 버전이 깔려 있든 성립해야
    하므로 실측 불일치에 의존하지 않고 OBV 참조값을 변조해 제외를 주입한다."""
    monkeypatch.setattr(verify_all, "VECTORS_DIR", tmp_path)
    original = verify_all._talib_direct

    def tampered(
        name: str,
        spec: IndicatorSpec,
        columns: Mapping[str, FloatArray],
        params: dict[str, int],
    ) -> dict[str, FloatArray]:
        result = original(name, spec, columns, params)
        if name != "OBV":
            return result
        return {k: np.where(np.arange(len(v)) == 0, np.nan, v) for k, v in result.items()}

    monkeypatch.setattr(verify_all, "_talib_direct", tampered)
    code = verify_all.main(["--mode", "nightly"])
    assert code == 1


def test_main_exits_zero_when_scope_excludes_the_known_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(verify_all, "VECTORS_DIR", tmp_path)
    passing = [n for n in verify_all.VERIFIABLE_NAMES if n not in _expected_exclusions()]
    assert passing
    monkeypatch.setattr(verify_all, "sample_names", lambda k, **_: tuple(passing))
    code = verify_all.main(["--mode", "ci", "--sample", "10"])
    assert code == 0


# --- DEEPEN(task-2927): 수치 성능 단언 — 전체 3자 교차검증 실행 지연 --------


def _verification_latencies_ms(perf_budget: PerfBudget, iterations: int = 10) -> list[float]:
    # task-7434: process_time-based perf_budget samples instead of raw
    # wall-clock perf_counter() -- avoids xdist core-contention noise.
    samples = [s.cpu_ms for s in perf_budget.samples(verify_all.run_verification, n=iterations)]
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


_FULL_VERIFICATION_BUDGET_MS = 800.0


def test_verify_indicator_rejects_unknown_indicator_name() -> None:
    """VERIFIABLE_NAMES 밖의 지표명은 조용히 빈 결과가 아니라 KeyError로 거부된다."""
    with pytest.raises(KeyError):
        verify_all.verify_indicator("NOT_A_REAL_INDICATOR", verify_all.default_datasets())


def test_write_snapshot_rejects_unknown_indicator_name(tmp_path: Path) -> None:
    dataset = verify_all.default_datasets()[0]
    with pytest.raises(KeyError):
        verify_all.write_snapshot("NOT_A_REAL_INDICATOR", dataset, vectors_dir=tmp_path)


def test_main_rejects_invalid_mode_argument() -> None:
    """`--mode`는 nightly/ci만 허용 — 그 외 값은 argparse가 exit code 2로 거부한다."""
    with pytest.raises(SystemExit) as exc_info:
        verify_all.main(["--mode", "not-a-real-mode"])
    assert exc_info.value.code == 2


def test_talib_dependency_failure_propagates_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TA-Lib C 호출 자체가 예외를 던지면(라이브러리 버전 불일치 등) 삼켜서
    빈 mismatch 목록으로 위장하지 않고 그대로 전파한다(fail-closed, CLAUDE.md §3)."""

    def boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("injected TA-Lib failure")

    monkeypatch.setattr(talib, "SMA", boom)
    with pytest.raises(RuntimeError, match="injected TA-Lib failure"):
        verify_all.verify_indicator("SMA", verify_all.default_datasets())


@pytest.mark.perf
def test_full_verification_p95_latency_within_self_declared_budget(
    perf_budget: PerfBudget,
) -> None:
    """수치 성능 단언: ADR-2026-09-09-C Decision 1 예산표에 "3자 교차검증"
    전용 항목이 없다(가장 가까운 항목은 "지표 증분=일괄 동일", 지연 예산이
    아님) — 순수 인메모리 계산(디스크·네트워크 I/O 없음, 11개 지표 x 최대
    4개 데이터셋 x 파라미터 변형)이라는 사실 위에 자체 예산을 건다: 로컬
    실측 p95 ~87ms(2026-09-16, VERIFIABLE_NAMES 11종 전수) 대비 약 9배
    여유를 둔 800ms. 예산을 벗어나면 실측 환경 문제가 아니라 회귀(예:
    데이터셋·파라미터 조합의 우발적 폭증, TA-Lib 직접 호출 경로의 중복
    실행)로 본다."""
    samples = _verification_latencies_ms(perf_budget, iterations=10)
    p95_ms = _p95(samples)
    print(
        f"[IND-7g] run_verification() p95={p95_ms:.2f}ms "
        f"budget<{_FULL_VERIFICATION_BUDGET_MS:.0f}ms (n={len(samples)})"
    )
    assert p95_ms < _FULL_VERIFICATION_BUDGET_MS
