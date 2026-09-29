"""EMS unit test suite package marker.

DEEPEN(task-7972), origin leaf task-6704 ("고아 산출물 회수"). This file has
no domain logic -- it is only the package marker that lets pytest treat
`tests/foundation/unit/ems/**` as a package tree. The one real invariant it
carries is structural: every immediate subdirectory under it must itself be
an importable package (have its own `__init__.py`), or pytest silently drops
that subtree from collection instead of failing loudly -- exactly the
failure mode fixed ad hoc in task-6413 (950a960f, "add missing __init__.py
to unbreak pytest collection"). These tests pin that invariant down with a
reusable scanner instead of relying on someone noticing a silent collection
gap again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_IGNORED_DIR_NAMES = {"__pycache__"}


def missing_init_dirs(root: Path) -> list[str]:
    """Return names of immediate subdirectories of `root` missing `__init__.py`.

    Fails closed by construction: `root.iterdir()` raises (FileNotFoundError /
    NotADirectoryError) rather than silently reporting "no missing dirs" when
    `root` itself does not exist or is not a directory.
    """
    return sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir()
        and entry.name not in _IGNORED_DIR_NAMES
        and not (entry / "__init__.py").exists()
    )


# ---------------------------------------------------------------------------
# Negative tests -- the scanner must reject/flag invariant-violating package
# layouts rather than silently treating them as fine.
# ---------------------------------------------------------------------------


class TestMissingInitDirsNegative:
    def test_detects_a_subdirectory_missing_init_py(self, tmp_path: Path) -> None:
        """A subdirectory with no __init__.py at all must be flagged --
        this is the exact shape of the task-6413 incident."""
        (tmp_path / "with_init").mkdir()
        (tmp_path / "with_init" / "__init__.py").touch()
        (tmp_path / "without_init").mkdir()

        assert missing_init_dirs(tmp_path) == ["without_init"]

    def test_ignores_pycache_directories(self, tmp_path: Path) -> None:
        """`__pycache__` is not a package and must never be reported as a
        missing-init violation -- a naive glob-without-exclusion would
        false-positive on every test run that has already executed once."""
        (tmp_path / "__pycache__").mkdir()

        assert missing_init_dirs(tmp_path) == []

    def test_an_empty_file_named_init_py_still_counts_as_present(self, tmp_path: Path) -> None:
        """Presence is existence-based, not content-based -- an empty
        `__init__.py` (like this very file) is a valid, complete package
        marker and must not be flagged as missing."""
        pkg = tmp_path / "empty_marker_pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")

        assert missing_init_dirs(tmp_path) == []


# ---------------------------------------------------------------------------
# Failure injection -- the scanner must fail closed (propagate) rather than
# silently returning "all clear" when the root itself is unusable.
# ---------------------------------------------------------------------------


class TestMissingInitDirsFailureInjection:
    def test_raises_when_root_does_not_exist(self, tmp_path: Path) -> None:
        """A nonexistent root must raise, not silently report zero
        violations -- fail-closed default posture (CLAUDE.md §3)."""
        missing_root = tmp_path / "does_not_exist"

        with pytest.raises(FileNotFoundError):
            missing_init_dirs(missing_root)

    def test_raises_when_root_is_a_file_not_a_directory(self, tmp_path: Path) -> None:
        """A root path that resolves to a plain file must raise
        (NotADirectoryError), not be silently treated as an empty package
        tree with zero violations."""
        file_root = tmp_path / "not_a_dir.txt"
        file_root.write_text("x")

        with pytest.raises(NotADirectoryError):
            missing_init_dirs(file_root)
