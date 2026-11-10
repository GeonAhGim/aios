"""scripts/coverage_ratchet.py baseline 쓰기 게이트 단위 테스트 — task-9120.

test_coverage_ratchet.py(454줄)에 덧붙이면 500줄 경고선을 넘으므로 책임 단위로
분리했다. 검증 대상: 신뢰 가능한 컨텍스트(GITHUB_ACTIONS=true 또는
--allow-baseline-write)에서만 baseline 파일을 쓰고, 그 외에는 비교·판정만 하며
baseline 파일은 건드리지 않는다. 회귀(하락) 판정은 신뢰 여부와 무관하게 유지된다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


coverage_ratchet = _load_module("coverage_ratchet", SCRIPTS_DIR / "coverage_ratchet.py")


def _write_coverage_xml(tmp_path: Path, line_rate: float) -> Path:
    path = tmp_path / "coverage.xml"
    path.write_text(
        f'<?xml version="1.0" ?><coverage line-rate="{line_rate}" branch-rate="0"></coverage>',
        encoding="utf-8",
    )
    return path


def _write_baseline(tmp_path: Path, percent: float) -> Path:
    path = tmp_path / "coverage-baseline.txt"
    path.write_text(f"{percent:.2f}\n", encoding="utf-8")
    return path


def test_write_trust_accepts_github_actions_env_or_explicit_flag() -> None:
    assert coverage_ratchet.baseline_write_is_trusted(False, {"GITHUB_ACTIONS": "true"})
    assert coverage_ratchet.baseline_write_is_trusted(True, {})


def test_write_trust_rejects_local_context_and_non_true_env_values() -> None:
    # 음성: 로컬 자동 실행(플래그·환경변수 없음)과 "true"가 아닌 값은 신뢰하지 않는다.
    assert not coverage_ratchet.baseline_write_is_trusted(False, {})
    assert not coverage_ratchet.baseline_write_is_trusted(False, {"GITHUB_ACTIONS": "false"})
    assert not coverage_ratchet.baseline_write_is_trusted(False, {"GITHUB_ACTIONS": "1"})


def test_untrusted_first_run_does_not_create_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # 음성: 신뢰 불가 컨텍스트에서 baseline이 없으면 초기화하지 않고 SKIP(exit 0).
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    xml_path = _write_coverage_xml(tmp_path, 0.75)
    baseline_path = tmp_path / "coverage-baseline.txt"

    code = coverage_ratchet.main(
        ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]
    )

    assert code == 0
    assert not baseline_path.exists()
    assert "SKIP" in capsys.readouterr().out


def test_untrusted_rise_keeps_baseline_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 음성: 로컬에서 측정치가 올라도 baseline 파일은 그대로(오염 경로 차단).
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    xml_path = _write_coverage_xml(tmp_path, 0.90)
    baseline_path = _write_baseline(tmp_path, 75.00)

    code = coverage_ratchet.main(
        ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]
    )

    assert code == 0
    assert baseline_path.read_text(encoding="utf-8").strip() == "75.00"


def test_untrusted_drop_still_fails_regression_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 음성: 쓰기 게이트가 회귀 판정을 약화시키면 안 된다 — 하락은 여전히 exit 1.
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    xml_path = _write_coverage_xml(tmp_path, 0.60)
    baseline_path = _write_baseline(tmp_path, 75.00)

    code = coverage_ratchet.main(
        ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]
    )

    assert code == 1
    assert baseline_path.read_text(encoding="utf-8").strip() == "75.00"
