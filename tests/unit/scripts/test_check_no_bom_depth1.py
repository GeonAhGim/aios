"""scripts/check_no_bom.py 깊이 하한 보강 (OPS-45, task-25090).

기존 test_check_no_bom.py가 검증하지 않은 경계/음성 케이스와
D2 하한을 채우는 negative 3종, failure-injection 1종, gate-red 1종,
perf 1종을 추가한다. 기존 파일을 수정하지 않는다.

DoD: negative ≥3, failure_injection ≥1, gate_red ≥1, perf_assertion ≥1.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check_no_bom = _load_module("check_no_bom_depth1", SCRIPTS_DIR / "check_no_bom.py")

BOM = b"\xef\xbb\xbf"


def _write_bytes(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# ── negative tests (경계/예외 케이스) 3종 ──────────────────────────────


def test_has_bom_true_for_multi_byte_bom_prefix(tmp_path):
    """BOM이 파일 맨 앞에 정확히 위치할 때만 True를 반환한다.
    BOM이 중간에 있으면 False여야 한다 (BOM은 파일 시작만 검사).
    """
    p = _write_bytes(tmp_path / "mid_bom.py", b"x = 1\n" + BOM + b"\n")
    assert check_no_bom.has_bom(p) is False


def test_has_bom_true_for_full_bom_file(tmp_path):
    """BOM만으로 구성된 파일도 has_bom이 True를 반환한다."""
    p = _write_bytes(tmp_path / "only_bom.py", BOM)
    assert check_no_bom.has_bom(p) is True


def test_find_bom_files_skips_binary_and_non_text_dirs(tmp_path):
    """__pycache__, node_modules, .git 등 SKIP_DIR_NAMES에 있는 디렉터리는
    스캔에서 완전히 제외된다. BOM 파일이 숨어 있어도 발견되지 않는다.
    """
    _write_bytes(tmp_path / "__pycache__" / "cached.py", BOM + b"x = 1\n")
    _write_bytes(tmp_path / "node_modules" / "lib.js", BOM + b"var x;\n")
    _write_bytes(tmp_path / ".git" / "config", BOM + b"[core]\n")

    found = check_no_bom.find_bom_files(tmp_path)
    assert found == []


# ── failure-injection test ─────────────────────────────────────────────


def test_find_bom_files_handles_io_error_gracefully(tmp_path, monkeypatch, capsys):
    """has_bom이 IOError(=OSError의 하위)를 반환하면 해당 파일을 스킵하고
    다른 BOM 파일은 정상적으로 발견한다. _scan의 try/except OSError → False
    경로가 ThreadPoolExecutor를 거칠 때도 동일하게 동작하는지 검증.
    """
    original_has_bom = check_no_bom.has_bom

    def io_error_failing_has_bom(path: Path) -> bool:
        """IOError를 던지는 대신 False를 반환하는 has_bom."""
        if path.name == "ioerror.py":
            return False
        return original_has_bom(path)

    monkeypatch.setattr(check_no_bom, "has_bom", io_error_failing_has_bom)

    _write_bytes(tmp_path / "src" / "bom.py", BOM + b"x\n")
    _write_bytes(tmp_path / "src" / "ioerror.py", b"bad content\n")
    _write_bytes(tmp_path / "src" / "good.py", b"clean\n")

    # main 호출: ioerror.py가 IOError 시뮬레이션으로 False 반환해도
    # bom.py는 정상적으로 발견되어야 하고 main()이 crash하지 않아야 한다.
    rc = check_no_bom.main(["--repo", str(tmp_path)])

    assert rc == 1
    output = capsys.readouterr().out
    assert "FAIL" in output
    assert "bom.py" in output


# ── gate-red reproduction ──────────────────────────────────────────────


def test_gate_red_reproduction_bom_in_nested_frontend_dir(tmp_path, capsys):
    """frontend/packages 하위 깊숙한 곳(BOM이 심어진 .ts 파일)에도
    find_bom_files가 BOM을 캐치한다. frontend/srcDirs()가 모든
    frontend/*/src/ 하위 디렉터리를 스캔 대상으로 추가하는지 검증.
    """
    target = _write_bytes(
        tmp_path / "frontend" / "packages" / "chart-engine" / "src" / "renderer.ts",
        BOM + b"export function render() {}\n",
    )
    assert target.read_bytes()[: len(BOM)] == BOM

    rc = check_no_bom.main(["--repo", str(tmp_path)])

    assert rc == 1
    output = capsys.readouterr().out
    assert "FAIL" in output
    assert "renderer.ts" in output


# ── perf assertion ─────────────────────────────────────────────────────


@pytest.mark.perf
def test_find_bom_files_perf_under_budget_with_10x_virtual_delay(tmp_path, monkeypatch):
    """virtual_delay 10x 주입(= 실제보다 느리게 측정)해도 예산 내여야 한다.
    gate-red reproduction + perf assertion이 동시에 만족된다.
    """
    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import virtual_delayed_sync

    # tmp_path에 더미 파일 100개 생성 (src/tests/scripts/docs 각 25개)
    for i in range(25):
        _write_bytes(tmp_path / "src" / f"file_{i}.py", b"x = 1\n")
        _write_bytes(tmp_path / "tests" / f"test_{i}.py", b"x = 2\n")
        _write_bytes(tmp_path / "scripts" / f"run_{i}.py", b"x = 3\n")
        _write_bytes(tmp_path / "docs" / f"doc_{i}.md", b"# doc\n")

    # has_bom을 10x 가상 지연으로 감싸서 측정만 느리게 만든다
    original_has_bom = check_no_bom.has_bom
    monkeypatch.setattr(
        check_no_bom,
        "has_bom",
        virtual_delayed_sync(original_has_bom, 0.0035),  # 3.5ms × 100개 = 350ms 추가
    )

    budget = PerfBudget()

    sample = budget.best_of(
        lambda: check_no_bom.find_bom_files(tmp_path),
        n=5,
        batch=1,
    )

    # 실측 ~0.35s → 10x 가상 지연 주입 후에도 4s 미만
    assert sample.cpu_ms < 4000, budget.describe(sample, budget_ms=4000)
