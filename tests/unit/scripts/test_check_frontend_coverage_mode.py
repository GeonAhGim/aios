"""check_frontend_coverage_mode -- 성능 단언이 있는 변경 vitest 파일만 고른다."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "check_frontend_coverage_mode", ROOT / "scripts" / "check_frontend_coverage_mode.py"
)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_only_files_with_perf_assertions_are_selected(tmp_path, monkeypatch):
    web = tmp_path / "web"
    (web / "src").mkdir(parents=True)
    (web / "src" / "a.test.ts").write_text("const t = performance.now();\n", encoding="utf-8")
    (web / "src" / "b.test.ts").write_text("expect(1).toBe(1);\n", encoding="utf-8")
    (web / "src" / "c.test.tsx").write_text("perfBudgetMs(100);\n", encoding="utf-8")
    monkeypatch.setattr(mod, "WEB", web)
    got = mod.perf_sensitive(["src/a.test.ts", "src/b.test.ts", "src/c.test.tsx"])
    assert got == ["src/a.test.ts", "src/c.test.tsx"]


def test_no_targets_means_pass(monkeypatch):
    monkeypatch.setattr(mod, "changed_vitest_files", lambda base: [])
    assert mod.main([]) == 0
