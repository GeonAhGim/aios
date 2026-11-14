"""scripts/check_collection_determinism.py -- 해시 시드에 따라 수집 순서가 달라지는 파일 적발.

실제 tests/ 트리에는 아무것도 쓰지 않는다(병렬 실행과 간섭하고 __pycache__가 남는다):
모든 임시 파일은 tmp_path 아래에 만들고 그 경로를 root로 넘긴다.
"""

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


def _make(root: Path, name: str, text: str) -> str:
    folder = root / "tests"
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")
    return "tests/" + name


def test_frozenset_parametrize_is_flagged_and_sorted_list_is_not(tmp_path: Path) -> None:
    module = _load()
    stable = _make(tmp_path, "test_stable_tmp.py", STABLE)
    unstable = _make(tmp_path, "test_unstable_tmp.py", UNSTABLE)
    assert module.unstable_files([stable], tmp_path) == []
    assert module.unstable_files([unstable], tmp_path) == [unstable]


def test_no_changed_test_files_passes() -> None:
    module = _load()
    assert module.main(["--base", "HEAD"]) == 0
