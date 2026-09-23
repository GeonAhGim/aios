"""scripts/coverage_ratchet.py 단위 테스트 — PLT-37.

DoD: 첫 측정치를 baseline으로 커밋하고, 인위로 낮춘 coverage.xml을 넣으면
exit=1로 FAIL한다. coverage.xml을 읽기만 하는 순수 파서이므로 DB·네트워크
접근 없이 합성 Cobertura XML(tmp_path)만으로 검증한다.
"""
from __future__ import annotations

import importlib.util
import sys
import time
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


def test_read_current_coverage_percent_zero_byte_file_raises_not_generated(
    tmp_path: Path,
) -> None:
    path = tmp_path / "coverage.xml"
    path.write_bytes(b"")

    with pytest.raises(coverage_ratchet.CoverageReportNotGeneratedError):
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


def test_zero_byte_coverage_xml_fails_with_not_generated_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """DoD: a 0-byte coverage.xml must fail with a 'report not generated'
    message, not the raw XML parse error, and must not read like a baseline
    miss."""
    xml_path = tmp_path / "coverage.xml"
    xml_path.write_bytes(b"")
    baseline_path = _write_baseline(tmp_path, 80.00)

    exit_code = coverage_ratchet.main(
        ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "생성되지 않았다" in out
    assert "기준선 미달" not in out


def test_missing_coverage_xml_fails_with_not_generated_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    xml_path = tmp_path / "does-not-exist.xml"
    baseline_path = _write_baseline(tmp_path, 80.00)

    exit_code = coverage_ratchet.main(
        ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "생성되지 않았다" in out


def test_baseline_miss_by_a_hair_fails_with_baseline_miss_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """DoD: a well-formed coverage.xml whose line-rate is even 0.01%p past
    tolerance must fail with a 'baseline miss' message distinct from the
    'report not generated' message used for empty/missing input."""
    xml_path = _write_coverage_xml(tmp_path, 0.7949)  # 79.49, baseline 80.00 -> -0.51%p
    baseline_path = _write_baseline(tmp_path, 80.00)

    exit_code = coverage_ratchet.main(
        ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "기준선 미달" in out
    assert "생성되지 않았다" not in out


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


# ---------------------------------------------------------------------------
# 실패 주입 — baseline 갱신 쓰기가 중간에 실패해도 조용히 통과 보고하지 않는다
# ---------------------------------------------------------------------------


def test_baseline_write_failure_propagates_instead_of_silent_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DoD: 실패 주입 — 커버리지가 올라 baseline을 갱신해야 하는 경로에서 디스크
    풀/권한 오류로 쓰기가 실패하면, CI는 "OK"를 출력하며 조용히 넘어가는 대신
    예외가 그대로 전파되어 비정상 종료(fail-closed)해야 한다. `main()`은
    `CoverageRatchetError`만 잡으므로 쓰기 단계의 `OSError`는 잡히지 않고
    올라와야 한다 — 그렇지 않으면 baseline이 새 값으로 갱신되지 않았는데도
    다음 실행이 그 사실을 모른 채 이전 baseline과 비교하는 사고로 이어진다."""
    xml_path = _write_coverage_xml(tmp_path, 0.85)
    baseline_path = _write_baseline(tmp_path, 80.00)

    def _raise_disk_full(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr(Path, "write_text", _raise_disk_full)

    with pytest.raises(OSError, match="No space left on device"):
        coverage_ratchet.main(
            ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]
        )


# ---------------------------------------------------------------------------
# 수치 성능 단언 — 순수 파서/비교 경로는 CI 스텝에서 매 커밋 실행되므로 저지연이어야 한다
# ---------------------------------------------------------------------------


def test_main_p95_latency_within_budget(tmp_path: Path) -> None:
    """coverage.xml 파싱 + baseline 비교는 파일 I/O 두 번뿐인 순수 경로다 —
    CI가 매 커밋 이 스크립트를 실행하므로 100회 반복 p95가 50ms를 넘으면
    안 된다(로컬 SSD 기준 예산; ADR-2026-09-09-C D2 "성능 단언 1" 요건)."""
    xml_path = _write_coverage_xml(tmp_path, 0.80)
    baseline_path = _write_baseline(tmp_path, 80.00)
    args = ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]

    samples: list[float] = []
    for _ in range(100):
        start = time.perf_counter()
        exit_code = coverage_ratchet.main(args)
        samples.append(time.perf_counter() - start)
        assert exit_code == 0

    samples.sort()
    p95 = samples[int(len(samples) * 0.95) - 1]
    assert p95 < 0.05, f"p95={p95 * 1000:.2f}ms exceeds 50ms budget"
