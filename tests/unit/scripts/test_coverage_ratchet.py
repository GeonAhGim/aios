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


def _write_coverage_xml(
    tmp_path: Path,
    line_rate: float,
    name: str = "coverage.xml",
    lines_valid: int | None = None,
) -> Path:
    path = tmp_path / name
    lines_valid_attr = f' lines-valid="{lines_valid}"' if lines_valid is not None else ""
    path.write_text(
        f'<?xml version="1.0" ?><coverage line-rate="{line_rate}" branch-rate="0"'
        f"{lines_valid_attr}></coverage>",
        encoding="utf-8",
    )
    return path


def _write_baseline(
    tmp_path: Path,
    percent: float,
    name: str = "coverage-baseline.txt",
    lines_valid: int | None = None,
) -> Path:
    path = tmp_path / name
    body = f"{percent:.2f}\n"
    if lines_valid is not None:
        body += f"{lines_valid}\n"
    path.write_text(body, encoding="utf-8")
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


def test_first_run_initializes_baseline_from_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    xml_path = _write_coverage_xml(tmp_path, 0.75)
    baseline_path = tmp_path / "coverage-baseline.txt"

    exit_code = coverage_ratchet.main(
        [
            "--coverage-xml",
            str(xml_path),
            "--baseline",
            str(baseline_path),
            "--allow-baseline-write",
        ]
    )

    assert exit_code == 0
    assert baseline_path.read_text(encoding="utf-8").strip() == "75.00"


def test_baseline_not_changed_on_failure(tmp_path: Path) -> None:
    xml_path = _write_coverage_xml(tmp_path, 0.70)
    baseline_path = _write_baseline(tmp_path, 80.00)

    exit_code = coverage_ratchet.main(
        ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 1
    assert baseline_path.read_text(encoding="utf-8").strip() == "80.00"  # 실패 시 baseline 미변경


def test_drop_within_tolerance_passes_and_keeps_baseline(tmp_path: Path) -> None:
    xml_path = _write_coverage_xml(tmp_path, 0.7970)  # 80.00 - 0.30%p
    baseline_path = _write_baseline(tmp_path, 80.00)

    exit_code = coverage_ratchet.main(
        ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 0
    assert baseline_path.read_text(encoding="utf-8").strip() == "80.00"


def test_risen_coverage_ratchets_baseline_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    xml_path = _write_coverage_xml(tmp_path, 0.85)
    baseline_path = _write_baseline(tmp_path, 80.00)

    exit_code = coverage_ratchet.main(
        [
            "--coverage-xml",
            str(xml_path),
            "--baseline",
            str(baseline_path),
            "--allow-baseline-write",
        ]
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
# 부분 리포트 감지(task-7644) — pytest 조기중단으로 lines-valid가 급감하면
# line-rate가 우연히 baseline 이내여도 비교 자체를 fail-closed로 거부한다
# ---------------------------------------------------------------------------


def test_partial_report_below_min_lines_valid_ratio_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """DoD: coverage.xml의 line-rate 자체는 baseline 이내로 보여도, lines-valid가
    baseline 대비 최소 비율(기본 0.5) 밑으로 떨어지면 pytest 조기중단으로 인한
    부분 리포트로 간주해 비교를 거부한다(기준선 미달 메시지와 구분되는 메시지)."""
    xml_path = _write_coverage_xml(tmp_path, 0.95, lines_valid=2000)  # 20% of baseline's lines
    baseline_path = _write_baseline(tmp_path, 80.00, lines_valid=10000)

    exit_code = coverage_ratchet.main(
        ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "부분 커버리지 리포트" in out
    assert "기준선 미달" not in out
    assert baseline_path.read_text(encoding="utf-8") == "80.00\n10000\n"


def test_partial_report_guard_also_blocks_a_spurious_ratchet_up(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """DoD: 부분 리포트가 우연히 baseline보다 높은 line-rate를 보고해도(예: 소수
    파일만 임포트돼 그 파일들의 커버리지가 높음) lines-valid 급감을 감지하면
    baseline을 올리지 않는다 — 조용한 하향 래칫 재발보다 더 위험한, 잘못된
    상향 래칫을 막는다."""
    xml_path = _write_coverage_xml(tmp_path, 0.99, lines_valid=1500)
    baseline_path = _write_baseline(tmp_path, 80.00, lines_valid=10000)

    exit_code = coverage_ratchet.main(
        ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 1
    assert baseline_path.read_text(encoding="utf-8") == "80.00\n10000\n"


def test_lines_valid_drop_within_min_ratio_still_compares_normally(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """DoD: lines-valid가 baseline 대비 살짝만 줄어(최소 비율 이상) 정상 범위면
    부분 리포트로 오판하지 않고 평소대로 line-rate 비교를 계속한다."""
    xml_path = _write_coverage_xml(tmp_path, 0.789, lines_valid=6000)
    baseline_path = _write_baseline(tmp_path, 80.00, lines_valid=10000)

    exit_code = coverage_ratchet.main(
        ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 1
    assert "부분 커버리지 리포트" not in capsys.readouterr().out


def test_lines_valid_not_set_still_compares_normally(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """DoD: lines-valid 속성이 coverage.xml에 없으면(구 Cobertura) 비교 자체를
    멈추지 않고 평소대로 line-rate 비교만 수행한다."""
    xml_path = _write_coverage_xml(tmp_path, 0.789)  # lines_valid=None
    baseline_path = _write_baseline(tmp_path, 80.00)

    exit_code = coverage_ratchet.main(
        ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 1
    assert "부분 커버리지 리포트" not in capsys.readouterr().out


def test_first_run_with_lines_valid_persists_in_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DoD: 첫 실행에서 --allow-baseline-write와 함께 lines-valid가 있는 XML을
    읽으면 baseline도 percent + lines_valid 두 줄로 기록한다."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    xml_path = _write_coverage_xml(tmp_path, 0.75, lines_valid=12345)
    baseline_path = tmp_path / "coverage-baseline.txt"

    exit_code = coverage_ratchet.main(
        [
            "--coverage-xml",
            str(xml_path),
            "--baseline",
            str(baseline_path),
            "--allow-baseline-write",
        ]
    )

    assert exit_code == 0
    lines = baseline_path.read_text(encoding="utf-8").strip().splitlines()
    assert lines == ["75.00", "12345"]


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
            [
                "--coverage-xml",
                str(xml_path),
                "--baseline",
                str(baseline_path),
                "--allow-baseline-write",
            ]
        )
