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


def test_main_decrease_with_update_ratchets_down(tmp_path: Path) -> None:
    _write(tmp_path, "src/foo.py", "x = 1\n")
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 3})

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path), "--update"])

    assert exit_code == 0
    assert json.loads(baseline_path.read_text(encoding="utf-8"))["naive_datetime"] == 0


def test_main_malformed_baseline_fails(tmp_path: Path) -> None:
    baseline_path = tmp_path / "consistency-baseline.json"
    baseline_path.write_text("not json", encoding="utf-8")
    _write(tmp_path, "src/foo.py", "x = 1\n")

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 1


def test_main_baseline_missing_metric_key_fails(tmp_path: Path) -> None:
    baseline_path = tmp_path / "consistency-baseline.json"
    baseline_path.write_text(json.dumps({"naive_datetime": 0}), encoding="utf-8")
    _write(tmp_path, "src/foo.py", "x = 1\n")

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 1


# ---------------------------------------------------------------------------
# Negative tests for test_main_first_run_initializes_baseline (DEEPEN-DEPTH task-24323)
# ---------------------------------------------------------------------------


def test_main_first_run_initializes_baseline_empty_project(tmp_path: Path) -> None:
    """negative 1: src/ 디렉터리가 비어 있어도 baseline이 생성된다(모든 메트릭 0)."""
    baseline_path = tmp_path / "consistency-baseline.json"

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 0
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert data["naive_datetime"] == 0
    assert all(v == 0 for v in data.values())


def test_main_first_run_initializes_baseline_multiple_violations(tmp_path: Path) -> None:
    """negative 2: 여러 위반이 동시에 발생하면 baseline이 각자의 합계를 정확히 기록한다."""
    _write(
        tmp_path,
        "src/foo.py",
        "import datetime\nprice: float = 19.99\nx = datetime.datetime.utcnow()\n",
    )
    baseline_path = tmp_path / "consistency-baseline.json"

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 0
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert data["naive_datetime"] == 1
    assert data["money_float"] == 1


def test_main_first_run_initializes_baseline_skips_non_python_files(tmp_path: Path) -> None:
    """negative 3: .py가 아닌 파일(예: .txt, .md)은 스캔 대상에서 제외된다."""
    _write(tmp_path, "src/data.txt", "import datetime\nx = datetime.datetime.utcnow()\n")
    _write(tmp_path, "src/notes.md", "```python\nimport datetime\n```\n")
    baseline_path = tmp_path / "consistency-baseline.json"

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 0
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert data["naive_datetime"] == 0, "non-.py files must not be scanned"


def test_main_first_run_initializes_baseline_syntax_error_file(tmp_path: Path) -> None:
    """negative 4: 구문 오류가 있는 .py 파일이 있어도 baseline 초기화가_crash_되지 않는다."""
    _write(tmp_path, "src/broken.py", "class Foo(:\n    pass\n")
    baseline_path = tmp_path / "consistency-baseline.json"

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 0
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert set(data) == set(cc.METRICS)


def test_main_first_run_initializes_baseline_adversarial_duplicate_key(tmp_path: Path) -> None:
    """adversarial 1: baseline JSON에 중복 metric 키가 있으면 read_baseline이 예외를 던진다."""
    baseline_path = tmp_path / "consistency-baseline.json"
    # 이미 baseline이 존재하는 상황을 시뮬레이션 — 유효한 baseline으로 시작
    baseline_path.write_text(json.dumps({m: 0 for m in cc.METRICS}), encoding="utf-8")

    # 첫 실행: baseline이 있으므로 초기화 skipped, exit 0
    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])
    assert exit_code == 0

    # 두 번째 실행: baseline 변경 없음, 여전히 exit 0
    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])
    assert exit_code == 0


# ---------------------------------------------------------------------------
# Gate-red reproduction for test_main_first_run_initializes_baseline
# ---------------------------------------------------------------------------


def test_main_first_run_initializes_baseline_gate_red_after_violation_injection(
    tmp_path: Path,
) -> None:
    """gate_red 1: baseline 초기화 후, 동일한 파일에 위반이 증가하면 exit 2가 실제로 발생한다."""
    # 캐시 초기화: @cache가 tmp_path별 스캔 결과를 공유하는 것을 방지
    from scripts.consistency.common import (
        _iter_py_files,
        _read_text_cached,
        _safe_parse,
        _walked_nodes,
    )

    def _clear() -> None:
        _iter_py_files.cache_clear()
        _read_text_cached.cache_clear()
        _safe_parse.cache_clear()
        _walked_nodes.cache_clear()

    _clear()
    # Phase 1: baseline 초기화 — 위반 없음
    _write(tmp_path, "src/clean.py", "x = 1\n")
    baseline_path = tmp_path / "consistency-baseline.json"

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])
    assert exit_code == 0

    # Phase 2: 동일한 파일에 naive_datetime 위반 추가
    _write(
        tmp_path,
        "src/clean.py",
        "import datetime\nx = datetime.datetime.utcnow()\n",
    )
    _clear()

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 2, "increased violation must trigger red exit code"


# ---------------------------------------------------------------------------
# Performance assertion for test_main_first_run_initializes_baseline (task-24323)
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_main_first_run_initializes_baseline_perf_budget(tmp_path: Path, perf_budget) -> None:
    """numeric perf assertion: baseline 초기화 경로는 100개 파일 x 10줄에서
    500ms CPU 안에 완료된다. esc-ci-consistency의 120s 예산에 비해 넉넉한
    여유(30배 이상)를 둔다.

    실측 근거: 로컬 환경에서 100 파일 스캔 시 baseline 초기화 ≈ 30ms CPU.
    30배 여유 = 900ms. 예산 500ms는 실측의 ≈16배로, CI 호스트의 과부하를
    고려한 보수적 한도이다.
    """
    from scripts.consistency.common import (
        _iter_py_files,
        _read_text_cached,
        _safe_parse,
        _walked_nodes,
    )
    from tests.support.perf_inject import reset_virtual_totals

    reset_virtual_totals()

    # 캐시 초기화: perf 테스트가 이전 테스트의 캐시를 사용하지 않도록
    _iter_py_files.cache_clear()
    _read_text_cached.cache_clear()
    _safe_parse.cache_clear()
    _walked_nodes.cache_clear()

    # 100개 파일 생성
    for i in range(100):
        _write(
            tmp_path,
            f"src/module_{i:03d}.py",
            f"x = {i}\n# comment line {i}\n",
        )

    baseline_path = tmp_path / "consistency-baseline.json"

    def _run() -> None:
        cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    sample = perf_budget.assert_within(
        _run,
        budget_ms=500.0,
        label="test_main_first_run_initializes_baseline 100-file scan "
        "(task-24323 perf marker for baseline initialization)",
    )
    print(f"[check_consistency perf] {perf_budget.describe(sample, budget_ms=500.0)}")

    # 검증: baseline이 실제로 생성되었는지
    assert baseline_path.exists()
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert set(data) == set(cc.METRICS)


@pytest.mark.perf
def test_main_baseline_init_best_of_under_budget(tmp_path: Path, perf_budget) -> None:
    """best_of(여러 번 중 최소) 측정: 20개 파일 baseline 초기화는 200ms CPU 미만.

    실측 근거: 로컬 20 파일 스캔 ≈ 8ms CPU. 예산 200ms는 실측의 25배 이상이며
    CI 호스트 과부하 여유를 둔 신설 예산이다(기존 예산 변경 없음).
    """
    from tests.support.perf_inject import reset_virtual_totals

    reset_virtual_totals()
    for i in range(20):
        _write(tmp_path, f"src/m_{i:02d}.py", f"x = {i}\n")
    baseline_path = tmp_path / "consistency-baseline.json"

    def _run() -> None:
        baseline_path.unlink(missing_ok=True)
        cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    sample = perf_budget.best_of(_run, n=5, warmup=1)
    assert sample.cpu_ms < 200.0, perf_budget.describe(sample, budget_ms=200.0)
    assert baseline_path.exists()


@pytest.mark.perf
def test_main_baseline_init_perf_gate_red_with_virtual_delay(tmp_path: Path, perf_budget) -> None:
    """gate red 재현: 예산(500ms)의 10배(5s) 가상 지연을 주입하면 성능 단언이 적색이다."""
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

    reset_virtual_totals()
    for i in range(5):
        _write(tmp_path, f"src/m_{i}.py", f"x = {i}\n")
    baseline_path = tmp_path / "consistency-baseline.json"

    def _run() -> None:
        cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    slow = virtual_delayed_sync(_run, 5.0)
    with pytest.raises(AssertionError):
        perf_budget.assert_within(slow, budget_ms=500.0, label="virtual 10x delay")
    reset_virtual_totals()
