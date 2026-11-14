"""scripts/check_collection_determinism.py -- 해시 시드에 따라 수집 순서가 달라지는 파일 적발."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
NL = chr(10)
STABLE = NL.join(
    [
        "import pytest",
        "",
        "@pytest.mark.parametrize('s', ['A', 'B', 'C', 'D', 'E'])",
        "def test_a(s):",
        "    pass",
        "",
    ]
)
UNSTABLE = NL.join(
    [
        "import pytest",
        "",
        "VALUES = frozenset({'ALPHA', 'BRAVO', 'CHARLIE', 'DELTA', 'ECHO', 'FOXTROT'})",
        "",
        "@pytest.mark.parametrize('s', VALUES, ids=str)",
        "def test_a(s):",
        "    pass",
        "",
    ]
)


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_collection_determinism", ROOT / "scripts" / "check_collection_determinism.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make(name: str, text: str) -> str:
    folder = ROOT / "tests" / "_tmp_determinism"
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return str(path.relative_to(ROOT)).replace(chr(92), "/")


def test_frozenset_parametrize_is_flagged_and_sorted_list_is_not() -> None:
    module = _load()
    stable = _make("test_stable_tmp.py", STABLE)
    unstable = _make("test_unstable_tmp.py", UNSTABLE)
    try:
        assert module.unstable_files([stable]) == []
        assert module.unstable_files([unstable]) == [unstable]
    finally:
        for rel in (stable, unstable):
            (ROOT / rel).unlink(missing_ok=True)
        (ROOT / "tests" / "_tmp_determinism").rmdir()


def test_no_changed_test_files_passes() -> None:
    module = _load()
    assert module.main(["--base", "HEAD"]) == 0
