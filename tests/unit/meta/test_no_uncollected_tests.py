"""Guard: test functions must live in modules pytest actually collects (`test_*.py`).

On 2026-09-30 a leaf generator targeted `tests/**/__init__.py`, `conftest.py` and
`tests/support/*.py`; workers wrote ~15,000 lines of tests there. pytest never collected
them, so they never ran -- they only inflated static ratchets and turned CI red. This
test fails as soon as a test function appears in an uncollected module again.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[2]

# conftest modules whose test functions ARE executed: a sibling `test_*.py` re-exports them.
SHIMMED_CONFTESTS = {
    "integration/auth/conftest.py",
    "integration/core/db/conftest.py",
    "integration/foundation/research_data/conftest.py",
}


def _is_collected(path: Path) -> bool:
    return path.name.startswith("test_") or path.name.endswith("_test.py")


def _top_level_tests(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test_"
        ):
            if any("fixture" in ast.unparse(dec) for dec in node.decorator_list):
                continue  # a fixture that happens to be named test_* (e.g. `test_app`)
            names.append(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            names.extend(
                f"{node.name}.{n.name}"
                for n in node.body
                if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
                and n.name.startswith("test_")
            )
    return names


def _offenders(root: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if _is_collected(path) or rel in SHIMMED_CONFTESTS:
            continue
        if path.name not in {"__init__.py", "conftest.py"} and not rel.startswith("support/"):
            continue
        names = _top_level_tests(path)
        if names:
            found[rel] = names
    return found


def test_no_test_functions_in_uncollected_modules() -> None:
    offenders = _offenders(TESTS_ROOT)
    assert offenders == {}, (
        "test functions in modules pytest does not collect (move them into a test_*.py): "
        f"{offenders}"
    )


def test_guard_detects_a_test_in_an_init_module(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("def test_x():\n    assert True\n", "utf-8")
    (tmp_path / "pkg" / "test_ok.py").write_text("def test_y():\n    assert True\n", "utf-8")
    (tmp_path / "conftest.py").write_text(
        "class TestZ:\n    def test_z(self):\n        assert True\n", "utf-8"
    )
    assert _offenders(tmp_path) == {"conftest.py": ["TestZ.test_z"], "pkg/__init__.py": ["test_x"]}


def test_guard_ignores_helpers_and_fixtures(tmp_path: Path) -> None:
    (tmp_path / "support").mkdir()
    (tmp_path / "support" / "helpers.py").write_text("def make_thing():\n    return 1\n", "utf-8")
    (tmp_path / "conftest.py").write_text("def pool():\n    return None\n", "utf-8")
    assert _offenders(tmp_path) == {}


def test_guard_ignores_fixtures_named_like_tests(tmp_path: Path) -> None:
    source = "import pytest\n\n\n@pytest.fixture\ndef test_app():\n    return object()\n"
    (tmp_path / "conftest.py").write_text(source, "utf-8")
    assert _offenders(tmp_path) == {}
