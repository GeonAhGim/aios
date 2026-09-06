"""IND-7g — `reference/verify_all.py` 3자 교차검증(TA-Lib C ↔ 증분 ↔ 벡터) 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.3 IND-7g
DoD: 3자 일치 지표만 노출(스냅샷 대상), 불일치 지표는 제외되고 그 상세가
`VerificationReport.mismatches`에 남는다(무음 통과 금지).

`test_full_verification_matches_known_state`는 BBANDS가 `timeperiod=2`(파라미터
최솟값) 경계에서 실제로 제외됨을 고정한다 — 이는 우리 구현 버그가 아니라 TA-Lib
BBANDS의 분산 계산(`E[X^2]-E[X]^2` 방식)이 표본이 2개뿐일 때 우리 구현(`E[(X-평균)^2]`,
증분·벡터 엔진이 공유하는 산식)보다 상쇄오차에 더 취약해서 생기는 실측 결과다 —
정확히 IND-7g가 잡아내야 하는 종류의 불일치라 스킵하지 않고 명시적으로 고정한다.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pytest

from src.core.indicators.engine import incremental, vectorized
from src.core.indicators.reference import verify_all
from src.core.indicators.reference.verify_all import FloatArray
from src.core.indicators.spec import IndicatorSpec
from src.core.indicators.specs_talib import TALIB_SPECS


def test_verifiable_names_require_all_three_implementations() -> None:
    assert verify_all.VERIFIABLE_NAMES == (
        "ATR", "BBANDS", "CCI", "EMA", "MACD", "MFI", "OBV", "RSI", "SMA", "STOCH", "WILLR",
    )  # fmt: skip
    for name in verify_all.VERIFIABLE_NAMES:
        assert name in TALIB_SPECS
        assert name in vectorized._KERNELS
        assert name in incremental._STATES


def test_full_verification_matches_known_state() -> None:
    report = verify_all.run_verification()
    assert set(report.verified) | set(report.excluded) == set(verify_all.VERIFIABLE_NAMES)
    assert set(report.verified) & set(report.excluded) == set()
    assert report.excluded == ("BBANDS",)
    assert report.mismatches
    assert {m.name for m in report.mismatches} == {"BBANDS"}
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
    monkeypatch.setattr(verify_all, "VECTORS_DIR", tmp_path)
    code = verify_all.main(["--mode", "nightly"])
    assert code == 1  # BBANDS는 항상 제외되므로(위 known-state 테스트) 0이 아니어야 한다


def test_main_exits_zero_when_scope_excludes_the_known_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(verify_all, "VECTORS_DIR", tmp_path)
    passing = [n for n in verify_all.VERIFIABLE_NAMES if n != "BBANDS"]
    monkeypatch.setattr(verify_all, "sample_names", lambda k, **_: tuple(passing))
    code = verify_all.main(["--mode", "ci", "--sample", "10"])
    assert code == 0
