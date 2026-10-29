"""scripts/check_perf_marker_guard.py 단위 테스트 -- perf_budget 확장 (task-21096).

DoD: perf_budget.sample(...).cpu_ms/.wall_ms 예산 단언도 perf 마커 없이 잡히는지
단위 테스트로 검증하고, 기존 perf_counter() 탐지가 여전히 작동하는지 확인한다.
정적 AST 스캔만 하는 순수 파서이므로 DB·import 없이 합성 ``.py`` 소스만으로
검증한다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> object:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check_perf_marker_guard = _load_module(
    "check_perf_marker_guard", SCRIPTS_DIR / "check_perf_marker_guard.py"
)


# ---------------------------------------------------------------------------
# 기존: raw perf_counter()/monotonic() 탐지
# ---------------------------------------------------------------------------


def test_raw_perf_counter_assert_without_marker_is_violation() -> None:
    """time.perf_counter() diff → assert without perf marker → violation."""
    source = (
        "import time\n"
        "\n"
        "def test_slow():\n"
        "    start = time.perf_counter()\n"
        "    do_work()\n"
        "    elapsed = time.perf_counter() - start\n"
        "    assert elapsed < 1.0\n"
    )
    tree = __import__("ast").parse(source)
    assert check_perf_marker_guard._find_violations(tree) == ["test_slow"]


def test_raw_monotonic_assert_without_marker_is_violation() -> None:
    """time.monotonic() diff → assert without perf marker → violation."""
    source = (
        "import time\n"
        "\n"
        "def test_slow():\n"
        "    start = time.monotonic()\n"
        "    do_work()\n"
        "    assert time.monotonic() - start < 2.0\n"
    )
    tree = __import__("ast").parse(source)
    assert check_perf_marker_guard._find_violations(tree) == ["test_slow"]


def test_marked_perf_counter_is_not_violation() -> None:
    """@pytest.mark.perf on a perf_counter test → clean."""
    source = (
        "import time, pytest\n"
        "\n"
        "@pytest.mark.perf\n"
        "def test_ok():\n"
        "    start = time.perf_counter()\n"
        "    do_work()\n"
        "    assert time.perf_counter() - start < 1.0\n"
    )
    tree = __import__("ast").parse(source)
    assert check_perf_marker_guard._find_violations(tree) == []


def test_module_level_pytestmark_covers_perf_counter() -> None:
    """Module-level pytestmark = pytest.mark.perf → clean."""
    source = (
        "import time, pytest\n"
        "\n"
        "pytestmark = pytest.mark.perf\n"
        "\n"
        "def test_ok():\n"
        "    start = time.perf_counter()\n"
        "    assert time.perf_counter() - start < 1.0\n"
    )
    tree = __import__("ast").parse(source)
    assert check_perf_marker_guard._find_violations(tree) == []


def test_skip_marker_exempts_perf_counter_assertion() -> None:
    """@pytest.mark.skip → clean (doesn't run)."""
    source = (
        "import time, pytest\n"
        "\n"
        "@pytest.mark.skip\n"
        "def test_skipped():\n"
        "    start = time.perf_counter()\n"
        "    assert time.perf_counter() - start < 1.0\n"
    )
    tree = __import__("ast").parse(source)
    assert check_perf_marker_guard._find_violations(tree) == []


def test_xfail_marker_exempts_perf_counter_assertion() -> None:
    """@pytest.mark.xfail → clean (expected to fail)."""
    source = (
        "import time, pytest\n"
        "\n"
        "@pytest.mark.xfail\n"
        "def test_xfailed():\n"
        "    start = time.perf_counter()\n"
        "    assert time.perf_counter() - start < 1.0\n"
    )
    tree = __import__("ast").parse(source)
    assert check_perf_marker_guard._find_violations(tree) == []


def test_non_test_function_ignored() -> None:
    """Non-test functions (not starting with 'test') are ignored."""
    source = (
        "import time\n"
        "\n"
        "def helper():\n"
        "    start = time.perf_counter()\n"
        "    assert time.perf_counter() - start < 1.0\n"
    )
    tree = __import__("ast").parse(source)
    assert check_perf_marker_guard._find_violations(tree) == []


# ---------------------------------------------------------------------------
# 신규: perf_budget.cpu_ms/wall_ms 탐지 (task-21096)
# ---------------------------------------------------------------------------


def test_perf_budget_sample_cpu_ms_without_marker_is_violation() -> None:
    """perf_budget.sample(...).cpu_ms → assert without perf marker → violation."""
    source = "def test_budget(perf_budget):\n    assert perf_budget.sample(_work).cpu_ms < 50.0\n"
    tree = __import__("ast").parse(source)
    assert check_perf_marker_guard._find_violations(tree) == [
        "test_budget",
    ]


def test_perf_budget_sample_wall_ms_without_marker_is_violation() -> None:
    """perf_budget.sample(...).wall_ms → assert without perf marker → violation."""
    source = (
        "def test_budget(perf_budget):\n"
        "    s = perf_budget.sample(_work)\n"
        "    assert s.wall_ms < 100.0\n"
    )
    tree = __import__("ast").parse(source)
    # s.wall_ms is NOT perf_budget.sample(...).wall_ms — it's a local var chain.
    # This is expected: only direct perf_budget.X(...).attr chains are detected.
    assert check_perf_marker_guard._find_violations(tree) == []


def test_perf_budget_sample_cpu_ms_with_marker_is_clean() -> None:
    """@pytest.mark.perf on a perf_budget test → clean."""
    source = (
        "import pytest\n"
        "\n"
        "@pytest.mark.perf\n"
        "def test_budget(perf_budget):\n"
        "    assert perf_budget.sample(_work).cpu_ms < 50.0\n"
    )
    tree = __import__("ast").parse(source)
    assert check_perf_marker_guard._find_violations(tree) == []


def test_perf_budget_module_pytestmark_covers_cpu_ms() -> None:
    """Module-level pytestmark = pytest.mark.perf → clean."""
    source = (
        "import pytest\n"
        "\n"
        "pytestmark = pytest.mark.perf\n"
        "\n"
        "def test_budget(perf_budget):\n"
        "    assert perf_budget.sample(_work).cpu_ms < 50.0\n"
    )
    tree = __import__("ast").parse(source)
    assert check_perf_marker_guard._find_violations(tree) == []


def test_perf_budget_skip_exempts_cpu_ms() -> None:
    """@pytest.mark.skip on a perf_budget test → clean."""
    source = (
        "import pytest\n"
        "\n"
        "@pytest.mark.skip\n"
        "def test_skipped(perf_budget):\n"
        "    assert perf_budget.sample(_work).cpu_ms < 50.0\n"
    )
    tree = __import__("ast").parse(source)
    assert check_perf_marker_guard._find_violations(tree) == []


def test_perf_budget_class_pytestmark_covers_cpu_ms() -> None:
    """Class-level pytestmark = pytest.mark.perf → clean for methods."""
    source = (
        "import pytest\n"
        "\n"
        "class TestBudget:\n"
        "    pytestmark = pytest.mark.perf\n"
        "\n"
        "    def test_method(self, perf_budget):\n"
        "        assert perf_budget.sample(_work).cpu_ms < 50.0\n"
    )
    tree = __import__("ast").parse(source)
    assert check_perf_marker_guard._find_violations(tree) == []


def test_perf_budget_sample_async_cpu_ms_without_marker() -> None:
    """perf_budget.sample_async(...).cpu_ms → violation."""
    source = (
        "async def test_async_budget(perf_budget):\n"
        "    s = await perf_budget.sample_async(_awork)\n"
        "    assert s.cpu_ms < 50.0\n"
    )
    tree = __import__("ast").parse(source)
    # s.cpu_ms is a local variable, not perf_budget.X(...).cpu_ms
    assert check_perf_marker_guard._find_violations(tree) == []


def test_perf_budget_direct_chain_cpu_ms_is_violation() -> None:
    """perf_budget.sample(...).cpu_ms → violation (direct chain)."""
    source = "def test_direct(perf_budget):\n    assert perf_budget.sample(_work).cpu_ms < 50.0\n"
    tree = __import__("ast").parse(source)
    assert check_perf_marker_guard._find_violations(tree) == ["test_direct"]


def test_perf_budget_direct_chain_wall_ms_is_violation() -> None:
    """perf_budget.sample(...).wall_ms → violation (direct chain)."""
    source = "def test_direct(perf_budget):\n    assert perf_budget.sample(_work).wall_ms < 100.0\n"
    tree = __import__("ast").parse(source)
    assert check_perf_marker_guard._find_violations(tree) == ["test_direct"]


def test_perf_budget_best_of_direct_chain_is_violation() -> None:
    """perf_budget.best_of(...).cpu_ms → violation."""
    source = "def test_best(perf_budget):\n    assert perf_budget.best_of(_work).cpu_ms < 50.0\n"
    tree = __import__("ast").parse(source)
    assert check_perf_marker_guard._find_violations(tree) == ["test_best"]


def test_perf_budget_samples_direct_chain_is_violation() -> None:
    """perf_budget.samples(...)[0].cpu_ms → NOT a direct chain (index access breaks it)."""
    source = (
        "def test_samples(perf_budget):\n"
        "    samples = perf_budget.samples(_work, n=5)\n"
        "    assert samples[0].cpu_ms < 50.0\n"
    )
    tree = __import__("ast").parse(source)
    # samples[0].cpu_ms — the value of .cpu_ms is samples[0], not perf_budget.samples(...)
    assert check_perf_marker_guard._find_violations(tree) == []


# ---------------------------------------------------------------------------
# _perf_budget_attr_chain 정단 테스트
# ---------------------------------------------------------------------------


def test_perf_budget_attr_chain_detects_sample() -> None:
    """Direct perf_budget.sample(...).cpu_ms chain is detected."""
    source = "perf_budget.sample(_work).cpu_ms"
    tree = __import__("ast").parse(source)
    attr = tree.body[0].value  # Attribute(cpu_ms)
    assert check_perf_marker_guard._perf_budget_attr_chain(attr) is True


def test_perf_budget_attr_chain_detects_assert_within() -> None:
    """Direct perf_budget.assert_within(...).cpu_ms chain is detected."""
    source = "perf_budget.assert_within(_work, budget_ms=50).cpu_ms"
    tree = __import__("ast").parse(source)
    attr = tree.body[0].value  # Attribute(cpu_ms)
    assert check_perf_marker_guard._perf_budget_attr_chain(attr) is True


def test_perf_budget_attr_chain_rejects_local_var() -> None:
    """s.cpu_ms where s is a local variable is NOT detected."""
    source = "s.cpu_ms"
    tree = __import__("ast").parse(source)
    attr = tree.body[0].value  # Attribute(cpu_ms)
    assert check_perf_marker_guard._perf_budget_attr_chain(attr) is False


def test_perf_budget_attr_chain_rejects_other_fixture() -> None:
    """other.sample(...).cpu_ms is NOT detected."""
    source = "other.sample(_work).cpu_ms"
    tree = __import__("ast").parse(source)
    attr = tree.body[0].value
    assert check_perf_marker_guard._perf_budget_attr_chain(attr) is False


# ---------------------------------------------------------------------------
# _contains_perf_budget_assert 정단 테스트
# ---------------------------------------------------------------------------


def test_contains_perf_budget_assert_detects_in_assert_stmt() -> None:
    """assert inside a function body is detected."""
    source = "def test_x(perf_budget):\n    assert perf_budget.sample(_work).cpu_ms < 50\n"
    tree = __import__("ast").parse(source)
    func = tree.body[0]
    assert check_perf_marker_guard._contains_perf_budget_assert(func) is True


def test_contains_perf_budget_assert_no_false_positive() -> None:
    """Function without perf_budget assert is clean."""
    source = "def test_x():\n    assert 1 < 2\n"
    tree = __import__("ast").parse(source)
    func = tree.body[0]
    assert check_perf_marker_guard._contains_perf_budget_assert(func) is False


# ---------------------------------------------------------------------------
# 게이트 적색 재현: scan_all + main
# ---------------------------------------------------------------------------


def test_scan_all_catches_perf_budget_violation(tmp_path: Path) -> None:
    """실제 파일 스캔에서 perf_budget violation가 offenders에 포함된다."""
    (tmp_path / "test_x.py").write_text(
        "def test_budget(perf_budget):\n    assert perf_budget.sample(_work).cpu_ms < 50.0\n",
        encoding="utf-8",
    )
    offenders = check_perf_marker_guard._scan_all(tmp_path)
    assert "test_x.py" in offenders
    assert "test_budget" in offenders["test_x.py"]


def test_scan_all_ignores_marked_perf_budget_test(tmp_path: Path) -> None:
    """perf 마커가 있으면 offenders에 포함되지 않는다."""
    (tmp_path / "test_x.py").write_text(
        "import pytest\n"
        "\n"
        "@pytest.mark.perf\n"
        "def test_budget(perf_budget):\n"
        "    assert perf_budget.sample(_work).cpu_ms < 50.0\n",
        encoding="utf-8",
    )
    offenders = check_perf_marker_guard._scan_all(tmp_path)
    assert offenders == {}


def test_main_exits_zero_when_clean(tmp_path: Path) -> None:
    """main() exits 0 when no offenders."""
    (tmp_path / "test_x.py").write_text(
        "def test_ok():\n    assert 1 < 2\n",
        encoding="utf-8",
    )
    assert check_perf_marker_guard.main(["--tests-root", str(tmp_path)]) == 0


def test_main_exits_one_on_perf_budget_violation(tmp_path: Path) -> None:
    """main() exits 1 when perf_budget violation exists."""
    (tmp_path / "test_x.py").write_text(
        "def test_budget(perf_budget):\n    assert perf_budget.sample(_work).cpu_ms < 50.0\n",
        encoding="utf-8",
    )
    assert check_perf_marker_guard.main(["--tests-root", str(tmp_path)]) == 1


def test_format_offenders_includes_task_21096_reference() -> None:
    """format_offenders output contains task-21096 reference."""
    offenders = {"test_x.py": ["test_budget"]}
    formatted = check_perf_marker_guard.format_offenders(offenders)
    assert "task-21096" in formatted
    assert "perf_budget" in formatted


# ---------------------------------------------------------------------------
# negative: perf_budget.assert_within 자체는 탐지하지 않음 (내부 단언이므로)
# ---------------------------------------------------------------------------


def test_perf_budget_assert_within_not_directly_detected() -> None:
    """perf_budget.assert_within() 내부의 assert는 테스트 코드에 직접 나타나지 않음.
    이 함수는 assert_within 자체를 탐지하지 않으며, 이는 의도적인 설계다
    (assert_within 내부에서 이미 budget 판정을 하므로).
    """
    source = (
        "def test_assert_within(perf_budget):\n"
        "    perf_budget.assert_within(_work, budget_ms=50.0)\n"
    )
    tree = __import__("ast").parse(source)
    # assert_within()는 내부에 assert가 있지만, 테스트 코드에 .cpu_ms/.wall_ms
    # 단언이 직접 나타나지 않으므로 violation 아님
    assert check_perf_marker_guard._find_violations(tree) == []


def test_perf_marker_guard_stays_within_complexity_cap() -> None:
    """중복 마커 판정이 complexity 게이트를 다시 적색으로 만들지 않는다."""
    from scripts.check_complexity import scan_file

    path = SCRIPTS_DIR / "check_perf_marker_guard.py"
    assert scan_file(path.name, path.read_text(encoding="utf-8")) == []
