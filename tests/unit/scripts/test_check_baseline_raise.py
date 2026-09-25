"""scripts/check_baseline_raise.py 단위 테스트 -- task-5303.

check_code_language.py의 실행 흐름을 거치지 않고 code-language-baseline.txt를 직접
더 작은 값으로 커밋하면(4acc2620, c2770645/task-4328처럼) 그 스크립트의 하한 가드는
아예 발동하지 않는다 -- 이 push 가드는 독립적으로 재측정해 "baseline < 실측"을 잡는다.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import uuid
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


check_baseline_raise = _load_module("check_baseline_raise", SCRIPTS_DIR / "check_baseline_raise.py")


def _write_py(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def in_repo_dir():
    d = ROOT / f"_tmp_check_baseline_raise_test_{uuid.uuid4().hex}"
    d.mkdir()
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_baseline_matching_measured_passes(in_repo_dir: Path, tmp_path: Path) -> None:
    target = in_repo_dir / "src"
    _write_py(target / "a.py", "x = 1  # 한글 하나\n")
    baseline_path = tmp_path / "code-language-baseline.txt"
    baseline_path.write_text("1\n", encoding="utf-8")

    exit_code = check_baseline_raise.main(
        ["--target", str(target), "--baseline", str(baseline_path)]
    )

    assert exit_code == 0


def test_baseline_above_measured_passes(in_repo_dir: Path, tmp_path: Path) -> None:
    target = in_repo_dir / "src"
    _write_py(target / "a.py", "x = 1  # 한글 하나\n")
    baseline_path = tmp_path / "code-language-baseline.txt"
    baseline_path.write_text("50\n", encoding="utf-8")

    exit_code = check_baseline_raise.main(
        ["--target", str(target), "--baseline", str(baseline_path)]
    )

    assert exit_code == 0


# ---------------------------------------------------------------------------
# negative tests -- baseline이 실측을 과소평가하면 push 거부
# ---------------------------------------------------------------------------


def test_baseline_below_measured_rejects(in_repo_dir: Path, tmp_path: Path, capsys) -> None:
    """gate-red repro: 4acc2620/c2770645처럼 baseline 파일을 직접 0으로 커밋한 경우."""
    target = in_repo_dir / "src"
    _write_py(target / "a.py", "x = 1  # 한글 하나\ny = 2  # 한글 둘\n")
    baseline_path = tmp_path / "code-language-baseline.txt"
    baseline_path.write_text("0\n", encoding="utf-8")

    exit_code = check_baseline_raise.main(
        ["--target", str(target), "--baseline", str(baseline_path)]
    )

    out = capsys.readouterr().out
    assert exit_code == 2
    assert "FAIL" in out
    assert "understates" in out
    # 실패 시에도 baseline 파일 자체는 건드리지 않는다 (이 가드는 순수 판정기).
    assert baseline_path.read_text(encoding="utf-8").strip() == "0"


def test_missing_baseline_file_rejects(in_repo_dir: Path, tmp_path: Path, capsys) -> None:
    target = in_repo_dir / "src"
    _write_py(target / "a.py", "x = 1  # 한글\n")
    baseline_path = tmp_path / "does-not-exist-baseline.txt"

    exit_code = check_baseline_raise.main(
        ["--target", str(target), "--baseline", str(baseline_path)]
    )

    out = capsys.readouterr().out
    assert exit_code == 2
    assert "missing or unreadable" in out


def test_non_integer_baseline_file_rejects(in_repo_dir: Path, tmp_path: Path, capsys) -> None:
    """failure injection: baseline 파일이 정수가 아닌 내용으로 손상된 경우."""
    target = in_repo_dir / "src"
    _write_py(target / "a.py", "x = 1  # 한글\n")
    baseline_path = tmp_path / "code-language-baseline.txt"
    baseline_path.write_text("not-a-number\n", encoding="utf-8")

    exit_code = check_baseline_raise.main(
        ["--target", str(target), "--baseline", str(baseline_path)]
    )

    out = capsys.readouterr().out
    assert exit_code == 2
    assert "missing or unreadable" in out


def test_empty_target_refuses_to_trust_measurement(tmp_path: Path, capsys) -> None:
    """실측이 불가능한(파일 0개) target으로는 baseline 판정을 내리지 않는다 --
    scanned==0을 baseline>=0 통과로 오인하면 안 된다."""
    empty_target = tmp_path / "wrong-cwd-target"
    empty_target.mkdir()
    baseline_path = tmp_path / "code-language-baseline.txt"
    baseline_path.write_text("11326\n", encoding="utf-8")

    exit_code = check_baseline_raise.main(
        ["--target", str(empty_target), "--baseline", str(baseline_path)]
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "no .py files" in out
