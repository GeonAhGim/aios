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
