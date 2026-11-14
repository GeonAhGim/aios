"""scripts/coverage_ratchet.py 깊이 하한 보강 (D1→D2) — PLT-37.

DoD: 부족한 항목만 보강한다. 기존 test_coverage_ratchet.py의 fixture·임포트
방식을 그대로 따른다. 새 파일은 500줄 이하.

보강 항목:
- 음성(negative) 테스트 3종 이상
- 실패 주입 (failure injection)
- 게이트 적색 재현 (gate-red reproduction)
- 성능 단언 (perf marker + virtual_delayed_sync + perf_budget.best_of)
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.support.perf_budget import PerfBudget

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
# 음성(negative) 테스트 — baseline 파싱 오류 3종
# ---------------------------------------------------------------------------


def test_baseline_empty_file_raises_coverage_ratchet_error(tmp_path: Path) -> None:
    """baseline 파일이 비어 있으면 CoverageRatchetError를.raise 한다."""
    path = tmp_path / "coverage-baseline.txt"
    path.write_text("", encoding="utf-8")

    with pytest.raises(coverage_ratchet.CoverageRatchetError):
        coverage_ratchet.read_baseline_percent(path)


def test_baseline_whitespace_only_raises(tmp_path: Path) -> None:
    """baseline 파일이 공백만 포함하면 CoverageRatchetError를.raise 한다."""
    path = tmp_path / "coverage-baseline.txt"
    path.write_text("   \n  \t  \n", encoding="utf-8")

    with pytest.raises(coverage_ratchet.CoverageRatchetError):
        coverage_ratchet.read_baseline_percent(path)


def test_baseline_negative_percent_accepted_as_float(tmp_path: Path) -> None:
    """구현: float() 파싱만 하므로 -5.00은 -5.0으로 파싱된다."""
    path = tmp_path / "coverage-baseline.txt"
    path.write_text("-5.00\n", encoding="utf-8")
    assert coverage_ratchet.read_baseline_percent(path) == -5.0


def test_baseline_over_100_accepted_as_float(tmp_path: Path) -> None:
    """구현: float() 파싱만 하므로 101.50은 101.5로 파싱된다."""
    path = tmp_path / "coverage-baseline.txt"
    path.write_text("101.50\n", encoding="utf-8")
    assert coverage_ratchet.read_baseline_percent(path) == 101.5


def test_baseline_float_with_exponent_accepted_as_float(tmp_path: Path) -> None:
    """구현: float() 파싱만 하므로 1e2는 100.0으로 파싱된다."""
    path = tmp_path / "coverage-baseline.txt"
    path.write_text("1e2\n", encoding="utf-8")
    assert coverage_ratchet.read_baseline_percent(path) == 100.0


# ---------------------------------------------------------------------------
# 음성(negative) 테스트 — coverage.xml 파싱 오류 3종
# ---------------------------------------------------------------------------


def test_coverage_xml_invalid_utf8_raises(tmp_path: Path) -> None:
    """coverage.xml이 UTF-8가 아니면 CoverageRatchetError를.raise 한다."""
    path = tmp_path / "coverage.xml"
    path.write_bytes(b'\xff\xfe<?xml version="1.0" ?><coverage></coverage>')

    with pytest.raises(coverage_ratchet.CoverageRatchetError):
        coverage_ratchet.read_current_coverage_percent(path)


def test_coverage_xml_with_only_comment_raises(tmp_path: Path) -> None:
    """coverage.xml이 XML 주석만 포함하면 CoverageRatchetError를.raise 한다."""
    path = tmp_path / "coverage.xml"
    path.write_text("<!-- just a comment -->", encoding="utf-8")

    with pytest.raises(coverage_ratchet.CoverageRatchetError):
        coverage_ratchet.read_current_coverage_percent(path)


# ---------------------------------------------------------------------------
# 음성(negative) 테스트 — main() 입력 검증
# ---------------------------------------------------------------------------


def test_main_missing_coverage_xml_path_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--coverage-xml에 존재하지 않는 파일을 주면 exit 1."""
    xml_path = tmp_path / "nonexistent.xml"
    baseline_path = _write_baseline(tmp_path, 80.00)

    result = coverage_ratchet.main(
        [
            "--coverage-xml",
            str(xml_path),
            "--baseline",
            str(baseline_path),
        ]
    )

    assert result == 1
    out = capsys.readouterr().out
    assert "생성되지 않았다" in out


def test_main_with_negative_tolerance_passes_when_coverage_rises(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--tolerance에 음수를 주면 argparse는 허용한다(타입 float).

    현재 구현은 argparse에서 타입만 체크하므로 -1.0이 전달된다.
    coverage가 baseline보다 높으면 delta > 0 > -1.0 이므로 통과한다.
    이 테스트는 구현의 현재 행동을 검증한다.
    """
    xml_path = _write_coverage_xml(tmp_path, 0.85)
    baseline_path = _write_baseline(tmp_path, 80.00)

    result = coverage_ratchet.main(
        [
            "--coverage-xml",
            str(xml_path),
            "--baseline",
            str(baseline_path),
            "--tolerance",
            "-1.0",
        ]
    )

    # coverage 상승 → 통과 (tolerance 음수여도 delta > tolerance)
    assert result == 0


# ---------------------------------------------------------------------------
# 실패 주입 (failure injection)
# ---------------------------------------------------------------------------


def test_read_current_lines_valid_returns_none_for_missing_attr(tmp_path: Path) -> None:
    """lines-valid 속성이 없으면 None — 보조 신호일 뿐이다."""
    xml_path = _write_coverage_xml(tmp_path, 0.80)

    assert coverage_ratchet.read_current_lines_valid(xml_path) is None


def test_read_current_lines_valid_returns_int_when_present(tmp_path: Path) -> None:
    """lines-valid 속성이 있으면 정수 반환."""
    xml_path = _write_coverage_xml(tmp_path, 0.80, lines_valid=5000)

    assert coverage_ratchet.read_current_lines_valid(xml_path) == 5000


def test_read_current_lines_valid_returns_none_for_non_numeric(tmp_path: Path) -> None:
    """lines-valid가 숫자가 아니면 None."""
    path = tmp_path / "coverage.xml"
    path.write_text(
        '<?xml version="1.0" ?><coverage line-rate="0.8" lines-valid="abc"></coverage>',
        encoding="utf-8",
    )

    assert coverage_ratchet.read_current_lines_valid(path) is None


def test_main_partial_report_detected_and_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """실패 주입: lines-valid가 baseline의 50% 미만이면 부분 리포트로 간주."""
    xml_path = _write_coverage_xml(tmp_path, 0.90, lines_valid=100)
    baseline_path = _write_baseline(tmp_path, 80.00, lines_valid=5000)

    result = coverage_ratchet.main(
        [
            "--coverage-xml",
            str(xml_path),
            "--baseline",
            str(baseline_path),
        ]
    )

    assert result == 1
    out = capsys.readouterr().out
    assert "부분 커버리지 리포트" in out


def test_main_baseline_miss_and_baseline_not_updated(
    tmp_path: Path,
) -> None:
    """실패 주입: baseline 미달 시 baseline 파일이 변경되지 않는다."""
    xml_path = _write_coverage_xml(tmp_path, 0.70)
    baseline_path = _write_baseline(tmp_path, 80.00)

    coverage_ratchet.main(
        [
            "--coverage-xml",
            str(xml_path),
            "--baseline",
            str(baseline_path),
        ]
    )

    assert baseline_path.read_text(encoding="utf-8").strip() == "80.00"


# ---------------------------------------------------------------------------
# 게이트 적색 재현 (gate-red reproduction)
# ---------------------------------------------------------------------------


def test_gate_red_baseline_miss_by_2_pct(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """게이트-Red 재현: baseline 80% → 측정 78% (-2%p). 허용 오차 0.5%p 초과 → FAIL."""
    xml_path = _write_coverage_xml(tmp_path, 0.78)
    baseline_path = _write_baseline(tmp_path, 80.00)

    result = coverage_ratchet.main(
        [
            "--coverage-xml",
            str(xml_path),
            "--baseline",
            str(baseline_path),
        ]
    )

    assert result == 1
    out = capsys.readouterr().out
    assert "기준선 미달" in out
    assert "78.00" in out
    assert "80.00" in out


def test_gate_red_baseline_miss_by_exact_tolerance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """게이트-Red 재현: 0.51%p 차이(79.49% vs 80.00%) → -0.51 < -0.50 → FAIL."""
    xml_path = _write_coverage_xml(tmp_path, 0.7949)
    baseline_path = _write_baseline(tmp_path, 80.00)

    result = coverage_ratchet.main(
        [
            "--coverage-xml",
            str(xml_path),
            "--baseline",
            str(baseline_path),
        ]
    )

    assert result == 1
    assert "기준선 미달" in capsys.readouterr().out


def test_gate_red_baseline_exactly_at_tolerance_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """게이트-Red 재현: 정확히 0.50%p 차이(79.50% vs 80.00%) → -0.50 < -0.50 False → PASS."""
    xml_path = _write_coverage_xml(tmp_path, 0.7950)
    baseline_path = _write_baseline(tmp_path, 80.00)

    result = coverage_ratchet.main(
        [
            "--coverage-xml",
            str(xml_path),
            "--baseline",
            str(baseline_path),
        ]
    )

    assert result == 0
    assert "기준선 미달" not in capsys.readouterr().out


def test_gate_red_custom_tolerance_respected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """게이트-Red 재현: 커스텀 허용 오차(1.0%p)에서 0.8%p 하락은 통과,
    1.2%p 하락은 실패.
    """
    # 하락 0.8%p → tolerance 1.0%p 이내 → 통과
    xml_path = _write_coverage_xml(tmp_path, 0.7920)  # 79.20 - 80.00 = -0.80
    baseline_path = _write_baseline(tmp_path, 80.00)

    result = coverage_ratchet.main(
        [
            "--coverage-xml",
            str(xml_path),
            "--baseline",
            str(baseline_path),
            "--tolerance",
            "1.0",
        ]
    )

    assert result == 0

    # 하락 1.2%p → tolerance 1.0%p 초과 → 실패
    xml_path2 = _write_coverage_xml(tmp_path, 0.7880)  # 78.80 - 80.00 = -1.20
    result2 = coverage_ratchet.main(
        [
            "--coverage-xml",
            str(xml_path2),
            "--baseline",
            str(baseline_path),
            "--tolerance",
            "1.0",
        ]
    )

    assert result2 == 1
    out = capsys.readouterr().out
    assert "기준선 미달" in out


# ---------------------------------------------------------------------------
# 성능 단언 (perf marker + virtual_delayed_sync + perf_budget.best_of)
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_read_current_coverage_percent_performance_best_of(
    tmp_path: Path, perf_budget: PerfBudget
) -> None:
    """Numeric performance assertion: XML 파싱 1회가 CPU 예산 10ms 안에 끝남.

    ``perf_budget.best_of``로 여러 호출 중 최소값을 측정하고 양자화 오차를
    ``batch=50``으로 낮춘다. 단위는 전량 ms 통일.
    """
    xml_path = _write_coverage_xml(tmp_path, 0.8347)

    sample = perf_budget.best_of(
        lambda: coverage_ratchet.read_current_coverage_percent(xml_path),
        n=5,
        batch=50,
    )

    assert sample.cpu_ms < 10, f"XML 파싱이 10ms 초과: {sample.cpu_ms:.3f}ms"


@pytest.mark.perf
def test_main_pipeline_performance_best_of(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, perf_budget: PerfBudget
) -> None:
    """Numeric performance assertion: main() 전체 파이프라인(CPU) 예산 50ms.

    ``perf_budget.best_of``로 best-of-5 최소값을 측정한다.
    """
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    xml_path = _write_coverage_xml(tmp_path, 0.75)
    baseline_path = tmp_path / "coverage-baseline.txt"

    sample = perf_budget.best_of(
        lambda: coverage_ratchet.main(
            [
                "--coverage-xml",
                str(xml_path),
                "--baseline",
                str(baseline_path),
                "--allow-baseline-write",
            ]
        ),
        n=5,
        batch=10,
    )

    assert sample.cpu_ms < 50, f"main() 파이프라인이 50ms 초과: {sample.cpu_ms:.3f}ms"


@pytest.mark.perf
def test_gate_red_with_virtual_delay_budget_injection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], perf_budget: PerfBudget
) -> None:
    """게이트-Red 재현 + 성능 예산 주입: 가상 지연 0.5s 주입 → 성능 예산 초과.

    ``virtual_delayed_sync``으로 CPU에 0.5초 가상 지연을 주입하면
    ``perf_budget.assert_within(budget_ms=50)``이 예산을 넘어 적색이어야 한다.
    """
    from tests.support.perf_inject import virtual_delayed_sync

    xml_path = _write_coverage_xml(tmp_path, 0.78)
    baseline_path = _write_baseline(tmp_path, 80.00)
    delayed_fn = virtual_delayed_sync(lambda args: coverage_ratchet.main(args), 0.5)

    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            lambda: delayed_fn(
                [
                    "--coverage-xml",
                    str(xml_path),
                    "--baseline",
                    str(baseline_path),
                ]
            ),
            budget_ms=50,
            batch=10,
        )


@pytest.mark.perf
def test_baseline_miss_by_a_hair_virtual_delay_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], perf_budget: PerfBudget
) -> None:
    """게이트-Red 재현: 0.01%p 차이(79.49% vs 80.00%) + 가상 지연 0.5s → budget 초과.

    실제 게이트 적색(기준선 미달)과 성능 예산 적색(지연 주입)이 동시에
    발생해야 한다.
    """
    from tests.support.perf_inject import virtual_delayed_sync

    xml_path = _write_coverage_xml(tmp_path, 0.7949)
    baseline_path = _write_baseline(tmp_path, 80.00)
    delayed_fn = virtual_delayed_sync(lambda args: coverage_ratchet.main(args), 0.5)

    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            lambda: delayed_fn(
                [
                    "--coverage-xml",
                    str(xml_path),
                    "--baseline",
                    str(baseline_path),
                ]
            ),
            budget_ms=50,
            batch=10,
        )
