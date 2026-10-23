"""Proof-level tests for ``scripts.pytest_shard.shard``.

Invariants verified
-------------------
1. **Determinism** — repeated calls return identical results.
2. **Union = input** — every input file appears in exactly one shard.
3. **Intersection = empty** — no file appears in two distinct shards.
4. **Order preservation** — each shard keeps the sorted order.
5. **Edge cases** — total=1, empty input, single file, many shards.
"""

from __future__ import annotations

import hashlib
import random

import pytest

from scripts.pytest_shard import shard


class TestDeterminism:
    """Same input → same output across calls."""

    def test_repeated_calls_identical(self):
        files = ["b.py", "a.py", "c.py", "d.py", "e.py"]
        result_a = shard(files, 0, 3)
        result_b = shard(files, 0, 3)
        assert result_a == result_b

    def test_different_indices_yield_different_shards(self):
        files = [f"file_{i:03d}.py" for i in range(20)]
        s0 = shard(files, 0, 3)
        s1 = shard(files, 1, 3)
        s2 = shard(files, 2, 3)
        assert s0 != s1 or s1 != s2  # at least two differ


class TestUnionEqualsInput:
    """Every input file appears in exactly one shard."""

    def test_all_files_present(self):
        files = [f"tests/{i:03d}/test_mod.py" for i in range(100)]
        total = 4
        all_sharded: list[str] = []
        for idx in range(total):
            all_sharded.extend(shard(files, idx, total))
        assert sorted(all_sharded) == sorted(files)

    def test_no_file_lost_with_many_shards(self):
        files = [f"test_{i}.py" for i in range(500)]
        total = 7
        collected = set()
        for idx in range(total):
            collected.update(shard(files, idx, total))
        assert collected == set(files)


class TestIntersectionEmpty:
    """No file appears in two distinct shards."""

    def test_pairwise_disjoint(self):
        files = [f"tests/pkg{i}/test_*.py" for i in range(30)]
        total = 4
        for i in range(total):
            for j in range(i + 1, total):
                si = set(shard(files, i, total))
                sj = set(shard(files, j, total))
                assert si.isdisjoint(sj), f"Shard {i} and {j} share files"

    def test_empty_intersection_large_set(self):
        files = [f"test_{hashlib.sha256(f'f{i}'.encode()).hexdigest()[:8]}.py" for i in range(200)]
        total = 8
        for i in range(total):
            for j in range(i + 1, total):
                assert set(shard(files, i, total)).isdisjoint(shard(files, j, total))


class TestOrderPreservation:
    """Each shard keeps the sorted order of its files."""

    def test_sorted_order_preserved(self):
        files = ["z.py", "a.py", "m.py", "b.py", "y.py", "c.py"]
        for idx in range(3):
            result = shard(files, idx, 3)
            assert result == sorted(result)

    def test_large_set_order(self):
        files = [f"test_{i:04d}.py" for i in range(300)]
        random.shuffle(files)
        for idx in range(5):
            result = shard(files, idx, 5)
            assert result == sorted(result)


class TestEdgeCases:
    """Boundary conditions."""

    def test_total_one_returns_all(self):
        files = ["a.py", "b.py", "c.py"]
        assert shard(files, 0, 1) == sorted(files)

    def test_empty_input(self):
        assert shard([], 0, 3) == []

    def test_single_file(self):
        assert shard(["only.py"], 0, 1) == ["only.py"]
        assert shard(["only.py"], 1, 2) == []

    def test_more_shards_than_files(self):
        files = ["a.py", "b.py"]
        total = 5
        for idx in range(total):
            result = shard(files, idx, total)
            assert result in ([], ["a.py"], ["b.py"])
        # Only two shards get files
        non_empty = [shard(files, idx, total) for idx in range(total) if shard(files, idx, total)]
        assert len(non_empty) == 2

    def test_invalid_total_raises(self):
        with pytest.raises(ValueError, match="total must be >= 1"):
            shard(["a.py"], 0, 0)

    def test_invalid_index_raises(self):
        with pytest.raises(ValueError, match="index .* out of range"):
            shard(["a.py"], 1, 1)

    def test_negative_index_raises(self):
        with pytest.raises(ValueError, match="index .* out of range"):
            shard(["a.py"], -1, 3)


class TestCLI:
    """Command-line interface."""

    def test_cli_files_from_stdin(self, monkeypatch):
        import io
        from contextlib import redirect_stdout

        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO("test_a.py\ntest_b.py\ntest_c.py\n"),
        )
        out = io.StringIO()
        with redirect_stdout(out):
            from scripts.pytest_shard import main

            main(["--total", "2", "--index", "0", "--files-from-stdin"])
        lines = [line.strip() for line in out.getvalue().strip().splitlines()]
        # sorted: test_a, test_b, test_c → indices 0,1,2 → shard 0: a, c
        assert lines == ["test_a.py", "test_c.py"]

    def test_cli_literal_files(self, monkeypatch, capsys):
        from scripts.pytest_shard import main

        main(["--total", "2", "--index", "1", "z.py", "a.py", "m.py"])
        captured = capsys.readouterr()
        lines = [line.strip() for line in captured.out.strip().splitlines()]
        # sorted: a, m, z → indices 0,1,2 → shard 1: m
        assert lines == ["m.py"]


@pytest.mark.parametrize("total", [1, 2, 4, 9])
def test_duplicate_paths_still_form_partition(total):
    files = ["b.py", "a.py", "b.py", "c.py", "a.py"]
    parts = [shard(files, i, total) for i in range(total)]
    assert sorted(path for part in parts for path in part) == sorted(set(files))
    assert parts == [shard(list(reversed(files)), i, total) for i in range(total)]


@pytest.mark.parametrize("broken", [False, True])
def test_collection_manifest_subprocess(tmp_path, broken):
    import os
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts" / "pytest_shard.py"
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    perf: serial budget\n", encoding="utf-8"
    )
    (tmp_path / "test_sample.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('value', ['a b', 'x::y'])\n"
        "def test_normal(value): pass\n"
        "@pytest.mark.perf\n"
        "def test_perf(): pass\n",
        encoding="utf-8",
    )
    (tmp_path / "test_perf_only.py").write_text(
        "import pytest\n@pytest.mark.perf\ndef test_perf(): pass\n", encoding="utf-8"
    )
    manifest = tmp_path / "files.txt"
    manifest.write_text("stale.py\n", encoding="utf-8")
    if broken:
        (tmp_path / "test_broken.py").write_text(
            "raise RuntimeError('injected')\n", encoding="utf-8"
        )
    result = subprocess.run(
        [sys.executable, str(script), "--collect-output", str(manifest)],
        cwd=tmp_path,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    if broken:
        assert result.returncode != 0
        assert not manifest.exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert manifest.read_text(encoding="utf-8").splitlines() == ["test_sample.py"]
