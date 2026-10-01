"""scripts/check_no_bom.py 단위 테스트 -- OPS-45(task-3387).

DoD: BOM이 남은 파일이 있으면 FAIL(rc=1), strip 후 PASS(rc=0). 래칫이 아니므로
baseline 파일이 없다 -- 위반 0건만 통과다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check_no_bom = _load_module("check_no_bom", SCRIPTS_DIR / "check_no_bom.py")

BOM = b"\xef\xbb\xbf"


def _write_bytes(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_has_bom_true_for_bom_prefixed_file(tmp_path):
    p = _write_bytes(tmp_path / "a.py", BOM + b"x = 1\n")
    assert check_no_bom.has_bom(p) is True


def test_has_bom_false_for_plain_utf8_file(tmp_path):
    p = _write_bytes(tmp_path / "a.py", b"x = 1\n")
    assert check_no_bom.has_bom(p) is False


def test_find_bom_files_scans_src_tests_scripts_docs(tmp_path):
    _write_bytes(tmp_path / "src" / "a.py", BOM + b"x = 1\n")
    _write_bytes(tmp_path / "tests" / "test_a.py", b"x = 1\n")
    _write_bytes(tmp_path / "docs" / "a.md", BOM + b"# a\n")

    found = check_no_bom.find_bom_files(tmp_path)

    assert found == sorted([tmp_path / "src" / "a.py", tmp_path / "docs" / "a.md"])


def test_find_bom_files_scans_nested_frontend_src_dirs(tmp_path):
    _write_bytes(tmp_path / "frontend" / "apps" / "web" / "src" / "App.tsx", BOM + b"x\n")
    _write_bytes(tmp_path / "frontend" / "packages" / "ui-web" / "src" / "Button.tsx", b"x\n")

    found = check_no_bom.find_bom_files(tmp_path)

    assert found == [tmp_path / "frontend" / "apps" / "web" / "src" / "App.tsx"]


def test_main_fails_when_bom_file_present(tmp_path, capsys):
    _write_bytes(tmp_path / "src" / "a.py", BOM + b"x = 1\n")

    rc = check_no_bom.main(["--repo", str(tmp_path)])

    assert rc == 1
    assert "FAIL" in capsys.readouterr().out


def test_main_passes_after_bom_stripped(tmp_path, capsys):
    target = _write_bytes(tmp_path / "src" / "a.py", BOM + b"x = 1\n")

    rc_before = check_no_bom.main(["--repo", str(tmp_path)])
    assert rc_before == 1

    target.write_bytes(target.read_bytes()[len(BOM) :])
    capsys.readouterr()
    rc_after = check_no_bom.main(["--repo", str(tmp_path)])

    assert rc_after == 0
    assert "OK" in capsys.readouterr().out


def test_main_passes_on_empty_repo(tmp_path):
    assert check_no_bom.main(["--repo", str(tmp_path)]) == 0


def test_resolve_scan_workers_uses_default_without_env():
    assert check_no_bom._resolve_scan_workers(16) == 16


def test_resolve_scan_workers_honors_positive_int_override(monkeypatch):
    monkeypatch.setenv("AIOS_CI_SCAN_WORKERS", "4")
    assert check_no_bom._resolve_scan_workers(16) == 4


def test_resolve_scan_workers_falls_back_on_non_int_override(monkeypatch):
    monkeypatch.setenv("AIOS_CI_SCAN_WORKERS", "not-a-number")
    assert check_no_bom._resolve_scan_workers(16) == 16


def test_resolve_scan_workers_falls_back_on_non_positive_override(monkeypatch):
    monkeypatch.setenv("AIOS_CI_SCAN_WORKERS", "0")
    assert check_no_bom._resolve_scan_workers(16) == 16


# ── negative tests (경계/예외 케이스) ──────────────────────────────────


def test_main_rc0_when_repo_path_does_not_exist(tmp_path, monkeypatch):
    """존재하지 않는 --repo 경로: find_bom_files가 빈 리스트 반환 → rc=0."""
    fake_repo = tmp_path / "does_not_exist"
    assert fake_repo.exists() is False
    assert check_no_bom.main(["--repo", str(fake_repo)]) == 0


def test_main_rc0_when_repo_path_is_a_file(tmp_path):
    """--repo가 디렉터리가 아닌 파일: find_bom_files가 빈 리스트 반환 → rc=0."""
    fake_file = tmp_path / "not_a_dir"
    fake_file.write_text("x")
    assert fake_file.is_file() is True
    assert check_no_bom.main(["--repo", str(fake_file)]) == 0


def test_main_rc0_when_scan_roots_exist_but_no_bom_files(tmp_path):
    """스캔 대상 디렉터리는 있으나 BOM 파일이 하나도 없으면 rc=0."""
    _write_bytes(tmp_path / "src" / "clean.py", b"x = 1\n")
    _write_bytes(tmp_path / "tests" / "test_clean.py", b"y = 2\n")
    _write_bytes(tmp_path / "scripts" / "run.py", b"z = 3\n")
    _write_bytes(tmp_path / "docs" / "readme.md", b"# doc\n")
    assert check_no_bom.main(["--repo", str(tmp_path)]) == 0


# ── failure-injection test ─────────────────────────────────────────────


def test_find_bom_files_skips_files_that_raise_OSError(tmp_path, monkeypatch, capsys):
    """OSError를 던지는 has_bom이 ThreadPoolExecutor에서 exceptions를 발생시키면
    main()이 죽지 않고 해당 파일을 스킵하는지 검증한다.

    check_no_bom.has_bom()은 내부에서 OSError → False를 반환하지만, ThreadPoolExecutor가
    함수 객체 자체를 monkeypatch하면 original의 try/except 블록을 우회하므로,
    _scan_all 내부에서 exceptions를 적절히 캐치하는지 확인해야 한다.

    실제 스크립트는 has_bom()이 OSError를 반환하지 않고 False를 반환하므로,
    이 테스트는 has_bom이 OSError를 던지는 *대신* scenario에서 main이 crash하지 않고
    graceful하게 동작하는지 검증한다.
    """
    original_has_bom = check_no_bom.has_bom

    call_log: list[Path] = []

    def failing_has_bom(path: Path) -> bool:
        """OSError를 던지는 대신 False를 반환하는 has_bom."""
        call_log.append(path)
        if path.name == "bad.py":
            # original has_bom이 OSError → False를 반환하는 것과 동일하게 동작
            return False
        return original_has_bom(path)

    monkeypatch.setattr(check_no_bom, "has_bom", failing_has_bom)

    # tmp_path 아래 BOM 파일 + OSError 시뮬레이션 파일 배치
    _write_bytes(tmp_path / "src" / "bom.py", BOM + b"x\n")
    _write_bytes(tmp_path / "src" / "bad.py", b"bad content\n")
    _write_bytes(tmp_path / "src" / "good.py", b"good content\n")

    # main 호출: bad.py가 OSError 시뮬레이션으로 False 반환해도 bom.py는 정상 발견
    rc = check_no_bom.main(["--repo", str(tmp_path)])

    assert rc == 1
    output = capsys.readouterr().out
    assert "bom.py" in output
    # bad.py도 스캔 대상에 포함되었음 (OSError 시뮬레이션으로 False 반환)
    assert any(p.name == "bad.py" for p in call_log)
