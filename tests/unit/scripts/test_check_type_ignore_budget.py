"""scripts/check_type_ignore_budget.py 단위 테스트 — PLT-40.

DoD: 첫 측정치를 budget으로 커밋하고, `# type: ignore`가 budget보다 늘어난
합성 트리를 넣으면 exit=1로 FAIL한다. 정적 텍스트 스캔만 하는 순수 파서이므로
DB·import 없이 tmp_path에 합성한 `.py` 파일만으로 검증한다.
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


check_type_ignore_budget = _load_module(
    "check_type_ignore_budget", SCRIPTS_DIR / "check_type_ignore_budget.py"
)


def _write_py(tmp_path: Path, relative: str, content: str) -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_budget(tmp_path: Path, value: int, name: str = "type-ignore-budget.txt") -> Path:
    path = tmp_path / name
    path.write_text(f"{value}\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 순수 스캐너 함수
# ---------------------------------------------------------------------------


def test_count_type_ignores_counts_lines_with_comment(tmp_path: Path) -> None:
    _write_py(
        tmp_path,
        "a.py",
        "x = 1  # type: ignore[attr-defined]\n"
        "y = 2\n"
        "z = 3  # type: ignore\n",
    )

    assert check_type_ignore_budget.count_type_ignores(tmp_path) == 2


def test_count_type_ignores_excludes_venv_and_pycache(tmp_path: Path) -> None:
    _write_py(tmp_path, "a.py", "x = 1  # type: ignore\n")
    _write_py(tmp_path, ".venv/lib/b.py", "y = 2  # type: ignore\n")
    _write_py(tmp_path, "src/__pycache__/c.py", "z = 3  # type: ignore\n")

    assert check_type_ignore_budget.count_type_ignores(tmp_path) == 1


def test_count_type_ignores_no_matches_is_zero(tmp_path: Path) -> None:
    _write_py(tmp_path, "a.py", "x = 1\n")

    assert check_type_ignore_budget.count_type_ignores(tmp_path) == 0


def test_read_budget_missing_file_returns_none(tmp_path: Path) -> None:
    assert check_type_ignore_budget.read_budget(tmp_path / "type-ignore-budget.txt") is None


def test_read_budget_malformed_raises(tmp_path: Path) -> None:
    path = tmp_path / "type-ignore-budget.txt"
    path.write_text("not-a-number\n", encoding="utf-8")

    with pytest.raises(check_type_ignore_budget.TypeIgnoreBudgetError):
        check_type_ignore_budget.read_budget(path)


# ---------------------------------------------------------------------------
# main() — DoD 시나리오
# ---------------------------------------------------------------------------


def test_first_run_initializes_budget_from_measurement(tmp_path: Path) -> None:
    _write_py(tmp_path, "a.py", "x = 1  # type: ignore\ny = 2  # type: ignore\n")
    budget_path = tmp_path / "type-ignore-budget.txt"

    exit_code = check_type_ignore_budget.main(
        ["--root", str(tmp_path), "--budget-file", str(budget_path)]
    )

    assert exit_code == 0
    assert budget_path.read_text(encoding="utf-8").strip() == "2"


def test_increase_beyond_budget_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_py(tmp_path, "a.py", "x = 1  # type: ignore\ny = 2  # type: ignore\n")
    budget_path = _write_budget(tmp_path, 1)

    exit_code = check_type_ignore_budget.main(
        ["--root", str(tmp_path), "--budget-file", str(budget_path)]
    )

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert budget_path.read_text(encoding="utf-8").strip() == "1"  # 실패 시 budget 미변경


def test_decrease_below_budget_ratchets_down(tmp_path: Path) -> None:
    _write_py(tmp_path, "a.py", "x = 1  # type: ignore\n")
    budget_path = _write_budget(tmp_path, 5)

    exit_code = check_type_ignore_budget.main(
        ["--root", str(tmp_path), "--budget-file", str(budget_path)]
    )

    assert exit_code == 0
    assert budget_path.read_text(encoding="utf-8").strip() == "1"


def test_equal_to_budget_passes_and_keeps_budget(tmp_path: Path) -> None:
    _write_py(tmp_path, "a.py", "x = 1  # type: ignore\n")
    budget_path = _write_budget(tmp_path, 1)

    exit_code = check_type_ignore_budget.main(
        ["--root", str(tmp_path), "--budget-file", str(budget_path)]
    )

    assert exit_code == 0
    assert budget_path.read_text(encoding="utf-8").strip() == "1"
