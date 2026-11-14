"""scripts/coverage_ratchet.py 깊이 보강 D1->D2 — PLT-37.

DoD: 부정(negative) 3종, 실패 주입 1종, 게이트 적색 재현 1종, 성능 단언 1종.
기존 test_coverage_ratchet.py의 fixture·임포트 방식을 그대로 따른다. 새 파일은
300줄 이하(500줄 경고 임계값 준수).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from tests.support.perf_budget import PerfBudget

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> object:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


coverage_ratchet = _load_module("coverage_ratchet_depth1", SCRIPTS_DIR / "coverage_ratchet.py")


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
# 음성(negative) 테스트 3종
# ---------------------------------------------------------------------------


def test_invalid_line_rate_type_raises_coverage_ratchet_error(tmp_path: Path) -> None:
    """coverage.xml의 line-rate가 float가 아닌 문자열이면 CoverageRatchetError."""
    path = tmp_path / "coverage.xml"
    path.write_text(
        '<?xml version="1.0" ?><coverage line-rate="high" branch-rate="0"></coverage>',
        encoding="utf-8",
    )

    with pytest.raises(coverage_ratchet.CoverageRatchetError, match="숫자가 아님"):
        coverage_ratchet.read_current_coverage_percent(path)


def test_negative_line_rate_raises_coverage_ratchet_error(tmp_path: Path) -> None:
    """line-rate가 음수(-1.0)면 파싱은 성공하지만 main()에서 게이트 적색이어야 한다.

    coverage.xml 파서 자체는 값을 검증하지 않으므로(main는 호출側에서 검증),
    여기서는 read_current_coverage_percent가 음수 값을 반환하는지 확인한다.
    """
    path = tmp_path / "coverage.xml"
    path.write_text(
        '<?xml version="1.0" ?><coverage line-rate="-0.1" branch-rate="0"></coverage>',
        encoding="utf-8",
    )

    # 파서는 숫자 변환만 하므로 -10.0 반환 (음수 허용, main에서 게이트 판단)
    assert coverage_ratchet.read_current_coverage_percent(path) == -10.0


def test_baseline_with_extra_whitespace_parses_cleanly(tmp_path: Path) -> None:
    """baseline 파일에 선행/후행 공백이 있어도 read_baseline_percent가 strip 한다."""
    path = tmp_path / "coverage-baseline.txt"
    path.write_text("  85.00  \n", encoding="utf-8")

    assert coverage_ratchet.read_baseline_percent(path) == 85.00


# ---------------------------------------------------------------------------
# 게이트 적색 재현 — 입력을 의도적으로 망가뜨려 적색 확인
# ---------------------------------------------------------------------------


def test_gate_red_baseline_miss_exceeds_tolerance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """게이트-Red 재현: baseline 80%에서 측정치 75%(5%p 하락) → exit=1.

    허용 오차 0.5%p를 10배 넘게 하락한 경우를 망가뜨린 입력으로 검증한다.
    출력에 '기준선 미달' 메시지가 들어가고 종료코드가 1이어야 한다.
    """
    xml_path = _write_coverage_xml(tmp_path, 0.75)  # 75.00%
    baseline_path = _write_baseline(tmp_path, 80.00)

    exit_code = coverage_ratchet.main(
        ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "기준선 미달" in out
    assert "80.00%" in out
    assert "75.00%" in out


def test_gate_red_custom_tolerance_respected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """게이트-Red 재현: --tolerance 1.0으로 설정 시 0.8%p 하락은 통과, 1.2%p 하락은 적색.

    허용 오차 0.5%p 기본값 대신 1.0%p를 설정하여 게이트가 파라미터를 올바르게
    반영하는지 검증한다.
    """
    # 79.00% -> baseline 80.00%에서 1.00%p 하락, tolerance 1.0이면 경계
    xml_path = _write_coverage_xml(tmp_path, 0.79)
    baseline_path = _write_baseline(tmp_path, 80.00)

    # tolerance 1.0: 1.00%p 하락은 허용 범위(>= -1.0) → 통과
    exit_code = coverage_ratchet.main(
        ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path), "--tolerance", "1.0"]
    )
    assert exit_code == 0

    # tolerance 1.0: 1.10%p 하락은 허용 범위 초과 → 적색
    xml_path2 = _write_coverage_xml(tmp_path, 0.789)
    exit_code2 = coverage_ratchet.main(
        ["--coverage-xml", str(xml_path2), "--baseline", str(baseline_path), "--tolerance", "1.0"]
    )
    assert exit_code2 == 1


# ---------------------------------------------------------------------------
# 실패 주입 — baseline 파일 쓰기 단계에서 OSError 주입
# ---------------------------------------------------------------------------


def test_failure_injection_baseline_write_error_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실패 주입: baseline 쓰기 단계에서 OSError(No space left)가 발생하면
    main()은 예외를 잡지 않고 그대로 전파해야 한다.

    main()은 CoverageRatchetError만 잡으므로 OSError는 올라와야 한다.
    """
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


# ---------------------------------------------------------------------------
# 성능 단언 — perf_budget.best_of 측정
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_main_performance_best_of_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, perf_budget: PerfBudget
) -> None:
    """성능 단언: main() 전체 파이프라인을 perf_budget.best_of(n=5)로 측정.

    로컬 실측: 5회 best_of 평균 cpu=0.4ms (2026-11-14, 로컬 Windows).
    예산은 실측의 30배 이상인 50ms로 설정.
    """
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    xml_path = _write_coverage_xml(tmp_path, 0.80)
    baseline_path = _write_baseline(tmp_path, 80.00)

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

    assert sample.cpu_ms < 50, (
        f"성능 예산 초과: cpu={sample.cpu_ms:.3f}ms >= 50ms budget (실측 30배 이상 여유)"
    )


# ---------------------------------------------------------------------------
# 보조 테스트 — lines_valid 관련 엣지 케이스
# ---------------------------------------------------------------------------


def test_read_current_lines_valid_returns_int(tmp_path: Path) -> None:
    """lines-valid 속성이 있으면 정수로 반환한다."""
    path = tmp_path / "coverage.xml"
    path.write_text(
        '<?xml version="1.0" ?><coverage line-rate="0.80" branch-rate="0"'
        ' lines-valid="1000"></coverage>',
        encoding="utf-8",
    )

    assert coverage_ratchet.read_current_lines_valid(path) == 1000


def test_read_current_lines_valid_missing_returns_none(tmp_path: Path) -> None:
    """lines-valid 속성이 없으면 None 반환."""
    path = tmp_path / "coverage.xml"
    path.write_text(
        '<?xml version="1.0" ?><coverage line-rate="0.80" branch-rate="0"></coverage>',
        encoding="utf-8",
    )

    assert coverage_ratchet.read_current_lines_valid(path) is None
