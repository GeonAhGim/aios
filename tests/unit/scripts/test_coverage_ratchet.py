"""scripts/coverage_ratchet.py 단위 테스트 — PLT-37.

DoD: 첫 측정치를 baseline으로 커밋하고, 인위로 낮춘 coverage.xml을 넣으면
exit=1로 FAIL한다. coverage.xml을 읽기만 하는 순수 파서이므로 DB·네트워크
접근 없이 합성 Cobertura XML(tmp_path)만으로 검증한다.
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


def _write_coverage_xml(tmp_path: Path, line_rate: float, name: str = "coverage.xml") -> Path:
    path = tmp_path / name
    path.write_text(
        f'<?xml version="1.0" ?><coverage line-rate="{line_rate}" branch-rate="0"></coverage>',
        encoding="utf-8",
    )
    return path


def _write_baseline(tmp_path: Path, percent: float, name: str = "coverage-baseline.txt") -> Path:
    path = tmp_path / name
    path.write_text(f"{percent:.2f}\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 순수 파서 함수
# ---------------------------------------------------------------------------


def test_read_current_coverage_percent_converts_line_rate(tmp_path: Path) -> None:
    xml_path = _write_coverage_xml(tmp_path, 0.8347)

    assert coverage_ratchet.read_current_coverage_percent(xml_path) == 83.47


def test_read_current_coverage_percent_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(coverage_ratchet.CoverageRatchetError):
        coverage_ratchet.read_current_coverage_percent(tmp_path / "does-not-exist.xml")


def test_read_current_coverage_percent_missing_line_rate_raises(tmp_path: Path) -> None:
    path = tmp_path / "coverage.xml"
    path.write_text('<?xml version="1.0" ?><coverage></coverage>', encoding="utf-8")

    with pytest.raises(coverage_ratchet.CoverageRatchetError):
        coverage_ratchet.read_current_coverage_percent(path)


def test_read_baseline_percent_missing_file_returns_none(tmp_path: Path) -> None:
    assert coverage_ratchet.read_baseline_percent(tmp_path / "coverage-baseline.txt") is None


def test_read_baseline_percent_malformed_raises(tmp_path: Path) -> None:
    path = tmp_path / "coverage-baseline.txt"
    path.write_text("not-a-number\n", encoding="utf-8")

    with pytest.raises(coverage_ratchet.CoverageRatchetError):
        coverage_ratchet.read_baseline_percent(path)


# ---------------------------------------------------------------------------
# main() — DoD 시나리오
# ---------------------------------------------------------------------------


def test_first_run_initializes_baseline_from_measurement(tmp_path: Path) -> None:
    xml_path = _write_coverage_xml(tmp_path, 0.75)
    baseline_path = tmp_path / "coverage-baseline.txt"

    exit_code = coverage_ratchet.main(
        ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 0
    assert baseline_path.read_text(encoding="utf-8").strip() == "75.00"


def test_lowered_coverage_beyond_tolerance_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    xml_path = _write_coverage_xml(tmp_path, 0.70)
    baseline_path = _write_baseline(tmp_path, 80.00)

    exit_code = coverage_ratchet.main(
        ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "80.00" in out and "70.00" in out
    assert baseline_path.read_text(encoding="utf-8").strip() == "80.00"  # 실패 시 baseline 미변경


def test_drop_within_tolerance_passes_and_keeps_baseline(tmp_path: Path) -> None:
    xml_path = _write_coverage_xml(tmp_path, 0.7970)  # 80.00 - 0.30%p
    baseline_path = _write_baseline(tmp_path, 80.00)

    exit_code = coverage_ratchet.main(
        ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 0
    assert baseline_path.read_text(encoding="utf-8").strip() == "80.00"


def test_risen_coverage_ratchets_baseline_up(tmp_path: Path) -> None:
    xml_path = _write_coverage_xml(tmp_path, 0.85)
    baseline_path = _write_baseline(tmp_path, 80.00)

    exit_code = coverage_ratchet.main(
        ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 0
    assert baseline_path.read_text(encoding="utf-8").strip() == "85.00"


def test_custom_tolerance_is_respected(tmp_path: Path) -> None:
    xml_path = _write_coverage_xml(tmp_path, 0.789)  # 78.90, baseline 80.00 -> -1.10%p
    baseline_path = _write_baseline(tmp_path, 80.00)

    exit_code = coverage_ratchet.main(
        [
            "--coverage-xml",
            str(xml_path),
            "--baseline",
            str(baseline_path),
            "--tolerance",
            "2.0",
        ]
    )

    assert exit_code == 0
