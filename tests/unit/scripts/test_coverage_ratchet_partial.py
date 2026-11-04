"""scripts/coverage_ratchet.py — 부분 리포트 감지 테스트 (task-7644).

DoD: coverage.xml의 line-rate 자체는 baseline 이내로 보여도, lines-valid가
baseline 대비 최소 비율(기본 0.5) 밑으로 떨어지면 pytest 조기중단으로 인한
부분 리포트로 간주해 비교 자체를 fail-closed로 거부한다.
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


@pytest.mark.perf
def test_lines_valid_not_set_still_compares_normally(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    perf_budget: PerfBudget,
) -> None:
    """DoD: lines-valid 속성이 coverage.xml에 없으면(구 Cobertura) 비교 자체를
    멈추지 않고 평소대로 line-rate 비교만 수행한다.

    main() 호출당 CPU 예산 50ms — ``perf_budget.assert_within()`` 경유, ``batch=10``
    으로 process_time 틱 양자화를 낮추고 단위는 ms로 통일.
    """
    xml_path = _write_coverage_xml(tmp_path, 0.789)  # lines_valid=None
    baseline_path = _write_baseline(tmp_path, 80.00)

    sample = perf_budget.assert_within(
        lambda: coverage_ratchet.main(
            ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]
        ),
        budget_ms=50,
        batch=10,
    )

    assert sample.result == 1
    assert "부분 커버리지 리포트" not in capsys.readouterr().out


def test_lines_valid_not_set_budget_injection_red(
    tmp_path: Path,
    perf_budget: PerfBudget,
) -> None:
    """Gate-red: 예산 50ms의 10배(500ms) 가상 지연 주입 → AssertionError."""
    from tests.support.perf_inject import virtual_delayed_sync

    xml_path = _write_coverage_xml(tmp_path, 0.789)
    baseline_path = _write_baseline(tmp_path, 80.00)
    delayed_fn = virtual_delayed_sync(lambda args: coverage_ratchet.main(args), 0.5)

    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            lambda: delayed_fn(["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]),
            budget_ms=50,
            batch=10,
        )
