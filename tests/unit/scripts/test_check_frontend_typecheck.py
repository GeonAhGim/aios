"""check_frontend_typecheck -- 변경된 프런트엔드 ts/tsx가 없으면 통과한다."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "check_frontend_typecheck", ROOT / "scripts" / "check_frontend_typecheck.py"
)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_no_changed_frontend_files_means_pass(monkeypatch):
    monkeypatch.setattr(mod, "changed_frontend_ts", lambda base: [])
    assert mod.main([]) == 0


def test_tsc_errors_fail_the_gate(monkeypatch, tmp_path):
    (tmp_path / "frontend" / "node_modules").mkdir(parents=True)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "WEB", tmp_path)
    monkeypatch.setattr(mod, "changed_frontend_ts", lambda base: ["frontend/a.ts"])
    monkeypatch.setattr(mod.shutil, "which", lambda name: "npx")

    class _R:
        returncode = 2
        stdout = "src/a.test.tsx(3,1): error TS6133: 'x' is declared but never used\n"
        stderr = ""

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _R())
    assert mod.main([]) == 1
