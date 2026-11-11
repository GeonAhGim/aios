"""파일 수 상쇄와 같은 LOC 구간 내 성장 회귀 (task-23318 / finding 393)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from scripts import check_code_ratchets as gate


def setup_tree(root: Path, loc: int = 520) -> tuple[Path, Path, list[str]]:
    source = root / "src" / "a.py"
    source.parent.mkdir()
    source.write_text("x = 1\n" * loc, encoding="utf-8")
    baseline = root / "baseline.json"
    counts = gate.counts_of(gate.scan_tree(root))
    gate.write_baseline(baseline, counts, {"src/a.py": loc})
    return source, baseline, ["--root", str(root), "--baseline", str(baseline)]


@pytest.mark.parametrize("loc", [521, 799, 900])
def test_growth_red_even_with_update(tmp_path: Path, loc: int, capsys) -> None:
    source, baseline, args = setup_tree(tmp_path)
    original = baseline.read_bytes()
    source.write_text("x = 1\n" * loc, encoding="utf-8")
    assert gate.main(args + ["--update"]) == 2
    assert "loc_growth src/a.py: 520 ->" in capsys.readouterr().out
    assert baseline.read_bytes() == original


def test_replacement_cannot_spend_deleted_file_budget(tmp_path: Path) -> None:
    source, _, args = setup_tree(tmp_path)
    source.rename(source.with_name("replacement.py"))
    assert gate.main(args) == 2


def test_decrease_persists_only_explicitly_then_blocks_regrowth(tmp_path: Path) -> None:
    source, baseline, args = setup_tree(tmp_path)
    source.write_text("x = 1\n" * 510, encoding="utf-8")
    original = baseline.read_bytes()
    assert gate.main(args) == 0
    assert baseline.read_bytes() == original
    assert gate.main(args + ["--update"]) == 0
    assert json.loads(baseline.read_text())["file_locs"] == {"src/a.py": 510}
    source.write_text("x = 1\n" * 511, encoding="utf-8")
    assert gate.main(args) == 2


def test_small_file_growth_allowed_and_budget_removed_after_shrink(tmp_path: Path) -> None:
    source, baseline, args = setup_tree(tmp_path)
    source.write_text("x = 1\n" * 500, encoding="utf-8")
    assert gate.main(args + ["--update"]) == 0
    assert json.loads(baseline.read_text())["file_locs"] == {}
    source.write_text("x = 1\n" * 501, encoding="utf-8")
    assert gate.main(args) == 2
    source.write_text("x = 1\n" * 400, encoding="utf-8")
    assert gate.main(args) == 0


@pytest.mark.parametrize(
    "bad", [None, [], {"src/a.py": True}, {"src/a.py": -1}, {"src/a.py": 500}, {"../a.py": 520}]
)
def test_corrupt_budget_fails_closed(tmp_path: Path, bad: object) -> None:
    _, baseline, args = setup_tree(tmp_path)
    data = json.loads(baseline.read_text())
    data["file_locs"] = bad
    baseline.write_text(json.dumps(data), encoding="utf-8")
    assert gate.main(args) == 1


def test_legacy_baseline_warns_without_write_then_migrates(tmp_path: Path, capsys) -> None:
    _, baseline, args = setup_tree(tmp_path)
    data = json.loads(baseline.read_text())
    del data["file_locs"]
    baseline.write_text(json.dumps(data), encoding="utf-8")
    original = baseline.read_bytes()
    assert gate.main(args) == 0
    assert "WARN:" in capsys.readouterr().out
    assert baseline.read_bytes() == original
    assert gate.main(args + ["--update"]) == 0
    assert json.loads(baseline.read_text())["file_locs"] == {"src/a.py": 520}


@pytest.mark.perf
def test_budget_check_reuses_scan_and_stays_within_gate_budget(tmp_path: Path, monkeypatch) -> None:
    _, _, args = setup_tree(tmp_path)

    def unexpected_scan(*args, **kwargs):
        raise AssertionError("per-file checks must reuse existing scan")

    monkeypatch.setattr(gate, "scan_locs", unexpected_scan)
    start = time.perf_counter()
    assert gate.main(args) == 0
    assert time.perf_counter() - start < 180
