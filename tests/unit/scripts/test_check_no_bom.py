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
