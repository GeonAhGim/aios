"""CLI baseline ratchet lifecycle -- task-10846, CONSIST-1.

Tests stay grouped by the consistency checker responsibility.
"""

from __future__ import annotations

import importlib.util
import json
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


cc = _load_module("check_consistency", SCRIPTS_DIR / "check_consistency.py")


def _write(tmp_path: Path, relative: str, content: str) -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# main() -- 래칫 DoD
# ---------------------------------------------------------------------------


def _write_baseline(tmp_path: Path, counts: dict[str, int]) -> Path:
    path = tmp_path / "consistency-baseline.json"
    full = {m: counts.get(m, 0) for m in cc.METRICS}
    path.write_text(json.dumps(full), encoding="utf-8")
    return path


def test_main_first_run_initializes_baseline(tmp_path: Path) -> None:
    _write(tmp_path, "src/foo.py", "import datetime\nx = datetime.datetime.utcnow()\n")
    baseline_path = tmp_path / "consistency-baseline.json"

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 0
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert data["naive_datetime"] == 1
    assert set(data) == set(cc.METRICS)


def test_main_increase_fails_red(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(
        tmp_path,
        "src/foo.py",
        "import datetime\nx = datetime.datetime.utcnow()\ny = datetime.datetime.utcnow()\n",
    )
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 1})

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    out = capsys.readouterr().out
    assert exit_code == 2
    assert "naive_datetime" in out
    unchanged = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert unchanged["naive_datetime"] == 1


def test_main_increase_fails_red_baseline_unchanged_on_red(tmp_path: Path) -> None:
    """negative: baseline 파일은 적색 발생 시 원래 값을 유지한다.

    baseline이 naive_datetime=2인데 현재 위반이 3으로 늘면 exit 2 +
    baseline은 그대로 2여야 한다. baseline이 갱신되면 래칫이 무력화된다.
    """
    _write(
        tmp_path,
        "src/foo.py",
        "import datetime\n"
        "x = datetime.datetime.utcnow()\n"
        "y = datetime.datetime.utcnow()\n"
        "z = datetime.datetime.utcnow()\n",
    )
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 2})

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 2
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert data["naive_datetime"] == 2


def test_main_increase_fails_red_multiple_metrics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """negative: 여러 지표가 동시에 증가하면 모든 지표를 FAIL 행에 나열한다.

    naive_datetime과 money_float가 동시에 늘어나면 두 지표 모두
    'increase'로 보고된다. baseline을 낮게 설정해 둘을 동시에 증가시킨다.
    """
    _write(
        tmp_path,
        "src/money.py",
        "amount: float = 10.5\n",
    )
    _write(
        tmp_path,
        "src/time.py",
        "import datetime\ndt = datetime.datetime.utcnow()\n",
    )
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 0, "money_float": 0})

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 2
    out = capsys.readouterr().out
    assert "naive_datetime" in out
    assert "money_float" in out


def test_main_increase_fails_red_partial_increase_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """negative: 일부 지표만 증가하고 나머지는 감소/동일하면 증가한 지표만 FAIL한다.

    naive_datetime은 늘고 money_float는 줄어드는 경우. baseline은 naive_datetime=1,
    money_float=1로 설정하고 naive_datetime은 2로 늘린다. money_float는 0으로 줄어든다.
    """
    _write(
        tmp_path,
        "src/foo.py",
        "import datetime\ndt = datetime.datetime.utcnow()\ndt2 = datetime.datetime.utcnow()\n",
    )
    _write(
        tmp_path,
        "src/bar.py",
        "x = 1\n",  # money_float 없음
    )
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 1, "money_float": 1})

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 2
    out = capsys.readouterr().out
    assert "naive_datetime" in out
    # money_float는 baseline(1) > 현재(0)이므로 증가하지 않음 → FAIL에 나오지 않아야 함
    assert (
        "money_float" not in out.split("naive_datetime")[0]
    )  # naive_datetime 앞부분에만 있는지 확인


def test_main_increase_fails_red_gate_red_with_perf_budget(tmp_path: Path, perf_budget) -> None:
    """gate-red reproduction + perf budget: perf_inject로 지연 10배 주입 후
    적색 재현 + best_of로 성능 측정.

    check_consistency.main()은 I/O가 적은 스캐너이므로 실제 지연은 없다.
    perf_inject.virtual_delayed_sync로 main() 호출에 가상 지연을 주입하면
    PerfBudget이 그 지연을 측정값에 합산한다. 10x 지연(예: 0.05s -> 0.5s)을
    주입해도 실제 테스트 실행 시간은 변하지 않지만, budget 단언은 지연을
    반영한 cpu_ms를 읽어야 한다.

    실측 근거: 가상 지연 없이 main() 100회 호출 ≈ 30ms CPU.
    10x 지연 주입 시 측정값 ≈ 30ms + 500ms = 530ms.
    예산 5000ms는 실측의 ≈9배로 CI 호스트 여유 포함.
    """
    from scripts.consistency.common import (
        _iter_py_files,
        _read_text_cached,
        _safe_parse,
        _walked_nodes,
    )
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

    reset_virtual_totals()

    _iter_py_files.cache_clear()
    _read_text_cached.cache_clear()
    _safe_parse.cache_clear()
    _walked_nodes.cache_clear()

    _write(
        tmp_path,
        "src/foo.py",
        "import datetime\nx = datetime.datetime.utcnow()\n",
    )
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 1})

    # main()에 가상 지연 0.05s(50ms) 주입 — 10배 지연 시 500ms 수준
    original_main = cc.main

    def _delayed_main(argv: list[str]) -> int:
        return virtual_delayed_sync(original_main, 0.05)(argv)

    sample = perf_budget.assert_within(
        lambda: _delayed_main(["--root", str(tmp_path), "--baseline", str(baseline_path)]),
        budget_ms=5000.0,
        n=3,
        label="test_main_increase_fails_red gate-red perf (task-24343)",
    )
    print(f"[check_consistency gate-red perf] {perf_budget.describe(sample, budget_ms=5000.0)}")

    # 실제 지연 없이 측정해도 적색이 뜨는지 확인 (baseline 유지)
    assert json.loads(baseline_path.read_text(encoding="utf-8"))["naive_datetime"] == 1


def test_main_increase_fails_red_adversarial_baseline_garbage_metric(
    tmp_path: Path,
) -> None:
    """adversarial: baseline에 실제 검사군이 아닌 가짜 metric 키가 있으면
    scan_all이 그 키를 무시하고 정상 실행된다.

    baseline에 'fake_metric': 999가 들어있어도 check_consistency는
    METRICS에 없는 키를 무시하므로 exit 0이어야 한다(첫 실행이 아니므로).
    """
    _write(tmp_path, "src/foo.py", "x = 1\n")
    baseline_path = tmp_path / "consistency-baseline.json"
    # METRICS에 없는 가짜 키 포함
    full = {m: 0 for m in cc.METRICS}
    full["fake_metric"] = 999
    baseline_path.write_text(json.dumps(full), encoding="utf-8")

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    # fake_metric은 METRICS에 없으므로 무시, 실제 위반 없음 → exit 0
    assert exit_code == 0


def test_main_decrease_without_update_keeps_baseline(tmp_path: Path) -> None:
    _write(tmp_path, "src/foo.py", "x = 1\n")
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 3})

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 0
    assert json.loads(baseline_path.read_text(encoding="utf-8"))["naive_datetime"] == 3


@pytest.mark.perf
def test_main_decrease_with_update_ratchets_down(tmp_path: Path, perf_budget) -> None:
    """Perf: local best-of-5 main(--update) ~10 ms CPU; budget 5000 ms (>30x); 10x delay is red."""
    from tests.support import perf_inject as pi

    _write(tmp_path, "src/foo.py", "x = 1\n")
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 3})
    argv = ["--root", str(tmp_path), "--baseline", str(baseline_path), "--update"]
    assert cc.main(argv) == 0
    assert json.loads(baseline_path.read_text(encoding="utf-8"))["naive_datetime"] == 0
    assert perf_budget.best_of(lambda: cc.main(argv), n=5).cpu_ms < 5000.0
    slow = perf_budget.best_of(lambda: pi.virtual_delayed_sync(cc.main, 50.0)(argv), n=3).cpu_ms
    assert slow >= 5000.0
    pi.reset_virtual_totals()


def test_main_malformed_baseline_fails(tmp_path: Path) -> None:
    """negative 1: baseline JSON이 완전히 무효하면 exit 1로 중단한다."""
    bp = tmp_path / "consistency-baseline.json"
    bp.write_text("not json", encoding="utf-8")
    _write(tmp_path, "src/foo.py", "x = 1\n")
    assert cc.main(["--root", str(tmp_path), "--baseline", str(bp)]) == 1


def test_main_malformed_baseline_fails_partial_json(tmp_path: Path) -> None:
    """negative 2: baseline이 부분적 JSON(중괄호만 열림)이면 exit 1로 중단한다."""
    bp = tmp_path / "consistency-baseline.json"
    bp.write_text('{"naive_datetime":', encoding="utf-8")
    _write(tmp_path, "src/foo.py", "x = 1\n")
    assert cc.main(["--root", str(tmp_path), "--baseline", str(bp)]) == 1


def test_main_malformed_baseline_fails_empty_file(tmp_path: Path) -> None:
    """negative 3: baseline이 빈 파일이면 exit 1로 중단한다."""
    bp = tmp_path / "consistency-baseline.json"
    bp.write_text("", encoding="utf-8")
    _write(tmp_path, "src/foo.py", "x = 1\n")
    assert cc.main(["--root", str(tmp_path), "--baseline", str(bp)]) == 1


@pytest.mark.perf
def test_main_malformed_baseline_fails_perf(tmp_path: Path, perf_budget) -> None:
    """perf: malformed baseline 처리는 O(1) 읽기이므로 매우 빠르다.

    best_of(5)로 여러 번 측정하고 budget 100ms 이내를 단언한다.
    """
    bp = tmp_path / "consistency-baseline.json"
    bp.write_text("not json", encoding="utf-8")
    _write(tmp_path, "src/foo.py", "x = 1\n")
    sample = perf_budget.assert_within(
        lambda: cc.main(["--root", str(tmp_path), "--baseline", str(bp)]),
        budget_ms=100.0,
        n=5,
        label="test_main_malformed_baseline_fails perf (task-24327)",
    )
    print(f"[check_consistency malformed perf] {perf_budget.describe(sample, budget_ms=100.0)}")


def test_main_baseline_missing_metric_key_fails(tmp_path: Path) -> None:
    baseline_path = tmp_path / "consistency-baseline.json"
    baseline_path.write_text(json.dumps({"naive_datetime": 0}), encoding="utf-8")
    _write(tmp_path, "src/foo.py", "x = 1\n")

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 1

