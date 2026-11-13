"""scripts/check_code_ratchets.py FAIL 출력 단언 -- task-23304.

loc_over_* 증가 시 실패 출력이 경로 사전순 --top(10) 절단으로 방금 임계값을 넘긴 파일을
빠뜨리던 진단 결함을 고정한다. 기존 test_check_code_ratchets.py는 LOC 파일 수 500줄 근접
(CLAUDE.md §6 #12)이라 여기 별도 파일로 둔다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check_code_ratchets = _load_module("check_code_ratchets", SCRIPTS_DIR / "check_code_ratchets.py")


def _empty_baseline() -> dict[str, int]:
    return {
        "skip_xfail": 0,
        "todo_fixme_xxx": 0,
        "not_implemented_error": 0,
        "loc_over_500": 0,
        "loc_over_800": 0,
        "loc_over_1000": 0,
    }


def test_loc_over_500_fail_output_lists_every_over_threshold_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """부정 케이스: 기준선 0에서 임계값 초과 파일 12개가 생기면 exit 2이고, --top(10)을
    넘는 목록이라도 경로 사전순 마지막 파일(zz_new.py)까지 전부 출력돼야 한다."""
    for i in range(11):
        path = tmp_path / "src" / f"f{i:02d}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n" * 501, encoding="utf-8")
    newest = tmp_path / "src" / "zz_new.py"
    newest.write_text("x = 1\n" * 501, encoding="utf-8")
    baseline_path = tmp_path / "code-ratchets-baseline.json"
    baseline_path.write_text(json.dumps(_empty_baseline()), encoding="utf-8")

    exit_code = check_code_ratchets.main(
        ["--root", str(tmp_path), "--baseline", str(baseline_path)]
    )

    out = capsys.readouterr().out
    assert exit_code == 2
    assert "src/zz_new.py" in out


def _run(tmp_path: Path, baseline: dict[str, int]) -> int:
    baseline_path = tmp_path / "code-ratchets-baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    argv = ["--root", str(tmp_path), "--baseline", str(baseline_path)]
    return int(check_code_ratchets.main(argv))


def _write_src(tmp_path: Path, name: str, lines: int) -> None:
    path = tmp_path / "src" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n" * lines, encoding="utf-8")


def test_no_over_threshold_files_exits_zero_without_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Negative: nothing over 500 lines -> exit 0 and no file path in the output."""
    _write_src(tmp_path, "small.py", 500)

    assert _run(tmp_path, _empty_baseline()) == 0
    assert "src/small.py" not in capsys.readouterr().out


def test_baseline_equal_to_current_count_exits_zero(tmp_path: Path) -> None:
    """Negative: a baseline equal to the current over-500 count is not an increase."""
    for i in range(3):
        _write_src(tmp_path, f"f{i}.py", 501)
    baseline = _empty_baseline()
    baseline["loc_over_500"] = 3

    assert _run(tmp_path, baseline) == 0


def test_loc_allow_marker_exempts_file_from_loc_ratchet(tmp_path: Path) -> None:
    """Negative: a ``# loc-allow:`` header keeps an oversized file out of the count."""
    path = tmp_path / "src" / "gen.py"
    path.parent.mkdir(parents=True)
    path.write_text("# loc-allow: generated table\n" + "x = 1\n" * 600, encoding="utf-8")

    assert _run(tmp_path, _empty_baseline()) == 0


def test_corrupt_baseline_file_fails_closed(tmp_path: Path) -> None:
    """Failure injection: an unparsable baseline must not read as a pass."""
    _write_src(tmp_path, "f.py", 10)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")

    try:
        exit_code = check_code_ratchets.main(["--root", str(tmp_path), "--baseline", str(bad)])
    except (ValueError, SystemExit, OSError):
        return
    assert exit_code != 0


def test_gate_red_breaking_input_flips_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Gate-red reproduction: the exit-2 assertion really depends on the input.

    A violating tree exits 2 and names the metric; raising the baseline (breaking the
    precondition) flips it to 0, so the exit-2 assertion would fail on the broken input.
    """
    for i in range(5):
        _write_src(tmp_path, f"red_{i}.py", 501)

    assert _run(tmp_path, _empty_baseline()) == 2
    assert "loc_over_500" in capsys.readouterr().out

    raised = _empty_baseline()
    raised["loc_over_500"] = 5
    assert _run(tmp_path, raised) == 0


@pytest.mark.perf
def test_perf_scan_file_within_budget(perf_budget: PerfBudget) -> None:
    """Perf: ``_scan_file`` on a 501-line module stays under 60 ms (best of 7).

    Measured locally 2026-11-13: wall ~1.8 ms, cpu 0 ms (below the 15.6 ms Windows clock
    quantum). The 60 ms budget leaves >30x headroom over the measured wall time.
    """
    reset_virtual_totals()
    text = "x = 1\n" * 501
    sample = perf_budget.best_of(lambda: check_code_ratchets._scan_file("a.py", text), n=7)

    assert sample.cpu_ms < 60.0, perf_budget.describe(sample, budget_ms=60.0)


@pytest.mark.perf
def test_perf_scan_file_goes_red_with_10x_virtual_delay(perf_budget: PerfBudget) -> None:
    """Gate-red reproduction for the perf budget: a virtual 10x delay (100 ms) must
    push the best-of sample past the 60 ms budget without really sleeping."""
    reset_virtual_totals()
    text = "x = 1\n" * 501
    delayed = virtual_delayed_sync(check_code_ratchets._scan_file, 0.1)
    sample = perf_budget.best_of(lambda: delayed("a.py", text), n=7)
    reset_virtual_totals()

    assert sample.cpu_ms >= 60.0, perf_budget.describe(sample, budget_ms=60.0)
