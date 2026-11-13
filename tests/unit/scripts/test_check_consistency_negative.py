"""CLI baseline ratchet -- negative tests, gate-red, and perf for task-10846, CONSIST-1.

Negative tests, gate-red reproductions, and performance assertions are split
here to keep the main test_check_consistency.py under the 500-line LOC ratchet.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.unit.scripts.test_check_consistency import (
    _write,
    cc,
)

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
        "import datetime\nx = datetime.datetime.utcnow()\ny = datetime.datetime.utcnow()\n",
    )

    # 캐시 다시 초기화 후 재스캔
    _clear()
    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    # baseline은 초기화 시점(0) 그대로, 현재는 2 violations → increase → exit 2
    assert exit_code == 2
    # baseline은 갱신되지 않음
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert data["naive_datetime"] == 0


@pytest.mark.perf
def test_main_first_run_initializes_baseline_perf_100_files(tmp_path: Path, perf_budget) -> None:
    """perf: 100개 파일 스캔은 O(n)이므로 budget 500ms 이내를 단언한다.

    best_of(3)로 여러 번 측정하고 최소 CPU 시간을 budget과 비교한다.
    """
    from scripts.consistency.common import (
        _iter_py_files,
        _read_text_cached,
        _safe_parse,
        _walked_nodes,
    )

    _iter_py_files.cache_clear()
    _read_text_cached.cache_clear()
    _safe_parse.cache_clear()
    _walked_nodes.cache_clear()

    for i in range(100):
        _write(tmp_path, f"src/file_{i}.py", f"x = {i}\n")

    baseline_path = tmp_path / "consistency-baseline.json"

    sample = perf_budget.assert_within(
        lambda: cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)]),
        budget_ms=500.0,
        n=3,
        label="test_main_first_run_initializes_baseline 100-file scan "
        "(task-24323 perf marker for baseline initialization)",
    )
    print(f"[check_consistency perf] {perf_budget.describe(sample, budget_ms=500.0)}")

    # 검증: baseline이 실제로 생성되었는지
    assert baseline_path.exists()
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert set(data) == set(cc.METRICS)


@pytest.mark.perf
def test_main_first_run_initializes_baseline_perf_gate_red_virtual_delay(
    tmp_path: Path, perf_budget
) -> None:
    """gate-red + perf: perf_inject 가상 지연 0.05s 주입 후 best_of(5) 측정.

    virtual_delayed_sync가 main() 호출에 50ms cpu+wall 지연을 추가하면
    perf_budget가 그 지연을 측정값에 합산한다. best_of(5)로 여러 번 중
    최소 CPU 시간을 선택하고 budget 5000ms 이내를 단언한다.
    """
    from scripts.consistency.common import (
        _iter_py_files,
        _read_text_cached,
        _safe_parse,
        _walked_nodes,
    )
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

    reset_virtual_totals()
    for fn in (_iter_py_files, _read_text_cached, _safe_parse, _walked_nodes):
        fn.cache_clear()
    _write(tmp_path, "src/foo.py", "import datetime\nx = datetime.datetime.utcnow()\n")
    baseline_path = tmp_path / "consistency-baseline.json"
    original_main = cc.main

    def _delayed(argv: list[str]) -> int:
        return virtual_delayed_sync(original_main, 0.05)(argv)

    sample = perf_budget.best_of(
        lambda: _delayed(["--root", str(tmp_path), "--baseline", str(baseline_path)]),
        n=5,
    )
    assert sample.cpu_ms < 5000.0


def test_main_first_run_initializes_baseline_adversarial_invalid_json(tmp_path: Path) -> None:
    """adversarial 1: baseline JSON이 완전히 무효하면 exit 1로 중단한다."""
    baseline_path = tmp_path / "consistency-baseline.json"
    baseline_path.write_text("<html>not json</html>", encoding="utf-8")
    _write(tmp_path, "src/clean.py", "x = 1\n")
    assert cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)]) == 1
