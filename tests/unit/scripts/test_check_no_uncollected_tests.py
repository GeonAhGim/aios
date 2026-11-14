"""scripts/check_no_uncollected_tests.py -- push 시점 게이트가 메타 가드와 같은 판정을 내린다."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_no_uncollected_tests", ROOT / "scripts" / "check_no_uncollected_tests.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_clean_tree_passes_the_gate() -> None:
    assert _load().find_offenders() == {}


def test_test_function_in_a_support_module_fails_the_gate(tmp_path: Path) -> None:
    support = tmp_path / "tests" / "support"
    support.mkdir(parents=True)
    (support / "helper.py").write_text("def test_oops():\n    pass\n", encoding="utf-8")
    assert _load().find_offenders(tmp_path) == {"support/helper.py": ["test_oops"]}
