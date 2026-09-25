"""FA-19 -- `scripts/check_idempotent_writes.py` scanner accuracy +
baseline-ratchet contract + perf budget + gate-red reproduction.

Loads the script as a module the same way `test_check_consistency.py`/
`test_check_position_key_central.py` do (``scripts/`` is not a package).
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"

# Measured full-repo scan (src/ + scripts/, ~800 files) is ~2.5s on a dev
# machine (no ADR-2026-09-09-C budget-table row fits a build-time AST scan --
# nearest comparable one-off batch job is "백테스트 1개월 M1 1심볼 3초"). This
# gate runs once per commit, not on a request hot path, so the budget below
# keeps ~2.4x headroom over the measured p95 for slower CI runners.
_SCAN_P95_BUDGET_SECONDS = 6.0


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


civ = _load_module("check_idempotent_writes", SCRIPTS_DIR / "check_idempotent_writes.py")


# --- scanner accuracy: positive hits ---------------------------------------


def test_flags_bare_insert_without_on_conflict():
    source = (
        "async def create(conn, key):\n"
        '    await conn.execute("INSERT INTO widgets (key) VALUES ($1)", key)\n'
    )
    hits = civ._scan_source(source, "fixture.py")
    assert [str(h) for h in hits] == [
        "fixture.py:2 — INSERT INTO without ON CONFLICT via .execute(...)"
    ]


def test_flags_multiline_insert_via_fetchrow():
    source = (
        "async def create(conn, key):\n"
        "    return await conn.fetchrow(\n"
        '        "INSERT INTO widgets (key) "\n'
        '        "VALUES ($1) RETURNING *",\n'
        "        key,\n"
        "    )\n"
    )
    hits = civ._scan_source(source, "fixture.py")
    assert len(hits) == 1
    assert "fetchrow" in hits[0].reason


def test_flags_executemany_insert():
    source = (
        "async def bulk(conn, rows):\n"
        '    await conn.executemany("INSERT INTO widgets (key) VALUES ($1)", rows)\n'
    )
    hits = civ._scan_source(source, "fixture.py")
    assert len(hits) == 1


# --- scanner accuracy: negatives (each documents a distinct exemption) ----


def test_ignores_insert_with_on_conflict():
    """negative -- an upsert is retry-safe by construction."""
    source = (
        "async def upsert(conn, key):\n"
        '    await conn.execute(\n'
        '        "INSERT INTO widgets (key) VALUES ($1) "\n'
        '        "ON CONFLICT (key) DO NOTHING",\n'
        "        key,\n"
        "    )\n"
    )
    assert civ._scan_source(source, "fixture.py") == []


def test_ignores_insert_with_allow_marker_on_same_line():
    """negative -- a reviewed, documented claim-first pattern is exempt."""
    source = (
        "async def create(conn, key):\n"
        '    await conn.execute("INSERT INTO widgets (key) VALUES ($1)", key)  '
        "# idempotent-allow: caller already claimed key via SELECT ... FOR UPDATE\n"
    )
    assert civ._scan_source(source, "fixture.py") == []


def test_ignores_insert_with_allow_marker_on_preceding_line():
    """negative -- the marker may sit on the line directly above the call."""
    source = (
        "async def create(conn, key):\n"
        "    # idempotent-allow: unique constraint on lines_digest + caller catches\n"
        '    await conn.execute("INSERT INTO widgets (key) VALUES ($1)", key)\n'
    )
    assert civ._scan_source(source, "fixture.py") == []


def test_ignores_dynamic_sql_it_cannot_verify():
    """negative -- an f-string first argument can't be read statically, so the
    scanner makes no claim either way rather than guessing."""
    source = (
        "async def create(conn, table, key):\n"
        '    await conn.execute(f"INSERT INTO {table} (key) VALUES ($1)", key)\n'
    )
    assert civ._scan_source(source, "fixture.py") == []


def test_ignores_select_statements():
    """negative -- only INSERT is in scope; SELECT/UPDATE/DELETE have their own
    (already-covered) idempotency mechanisms (standard-105 conditional UPDATE)."""
    source = (
        "async def read(conn, key):\n"
        '    return await conn.fetchrow("SELECT * FROM widgets WHERE key = $1", key)\n'
    )
    assert civ._scan_source(source, "fixture.py") == []


def test_exclusion_skips_migrations_directory(tmp_path: Path):
    migrations_file = tmp_path / "src" / "db" / "migrations" / "versions" / "abc_x.py"
    migrations_file.parent.mkdir(parents=True)
    migrations_file.write_text(
        'op.execute("INSERT INTO widgets (key) VALUES (\'seed\')")\n', encoding="utf-8"
    )
    hits = civ.scan_tree(tmp_path)
    assert hits == []


# --- baseline ratchet contract ---------------------------------------------


def test_read_baseline_missing_file_returns_none(tmp_path: Path):
    assert civ.read_baseline(tmp_path / "missing.json") is None


def test_read_baseline_malformed_json_raises(tmp_path: Path):
    path = tmp_path / "baseline.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(civ.BaselineError):
        civ.read_baseline(path)


def test_read_baseline_missing_count_key_raises(tmp_path: Path):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"hits": []}), encoding="utf-8")
    with pytest.raises(civ.BaselineError):
        civ.read_baseline(path)


def test_main_failure_injection_fails_closed_on_corrupt_baseline(tmp_path: Path, capsys):
    """Failure injection -- a corrupted baseline file must fail the gate
    (exit 1), never silently pass it (fail-closed default posture, CLAUDE.md
    §3)."""
    baseline = tmp_path / "baseline.json"
    baseline.write_text("", encoding="utf-8")
    exit_code = civ.main(["--root", str(tmp_path), "--baseline", str(baseline)])
    assert exit_code == 1


def test_main_red_gate_reproduction_fails_when_hits_exceed_baseline(tmp_path: Path):
    """Gate-red reproduction -- prove this check can actually turn red, not
    just report OK (I-10 wiring-proof posture)."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "writer.py").write_text(
        'async def create(conn, key):\n'
        '    await conn.execute("INSERT INTO widgets (key) VALUES ($1)", key)\n',
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"count": 0}), encoding="utf-8")

    exit_code = civ.main(["--root", str(tmp_path), "--baseline", str(baseline)])
    assert exit_code == 2

    # Fixing the file (adding ON CONFLICT) turns the same gate green again.
    (src / "writer.py").write_text(
        'async def create(conn, key):\n'
        '    await conn.execute(\n'
        '        "INSERT INTO widgets (key) VALUES ($1) ON CONFLICT (key) DO NOTHING", key\n'
        "    )\n",
        encoding="utf-8",
    )
    assert civ.main(["--root", str(tmp_path), "--baseline", str(baseline)]) == 0


def test_main_initializes_baseline_when_absent(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "writer.py").write_text(
        'async def create(conn, key):\n'
        '    await conn.execute("INSERT INTO widgets (key) VALUES ($1)", key)\n',
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline.json"
    assert civ.main(["--root", str(tmp_path), "--baseline", str(baseline)]) == 0
    data = json.loads(baseline.read_text(encoding="utf-8"))
    assert data["count"] == 1


def test_main_persists_decrease_only_with_update_flag(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "writer.py").write_text("async def noop():\n    pass\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"count": 5}), encoding="utf-8")

    assert civ.main(["--root", str(tmp_path), "--baseline", str(baseline)]) == 0
    assert json.loads(baseline.read_text(encoding="utf-8"))["count"] == 5

    assert civ.main(["--root", str(tmp_path), "--baseline", str(baseline), "--update"]) == 0
    assert json.loads(baseline.read_text(encoding="utf-8"))["count"] == 0


# --- performance budget ------------------------------------------------


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = max(0, -(-95 * len(ordered) // 100) - 1)
    return ordered[index]


@pytest.mark.perf
def test_scan_tree_p95_latency_within_budget_for_real_repo():
    # task-7434: scan_tree() walks the real repo tree (disk I/O), so this
    # stays on raw wall-clock perf_counter() -- process_time would not
    # capture I/O wait and would understate real scan latency.
    samples = []
    for _ in range(3):
        started = time.perf_counter()
        civ.scan_tree(ROOT)
        samples.append(time.perf_counter() - started)
    assert _p95(samples) < _SCAN_P95_BUDGET_SECONDS


@pytest.mark.perf
def test_perf_budget_guard_fails_on_injected_scan_delay(monkeypatch: pytest.MonkeyPatch):
    """Proves the assertion above is not vacuously green -- injecting a delay
    per scanned file must push elapsed time past the same budget. task-7434:
    the injected delay is a real `time.sleep()`, so this keeps raw
    wall-clock perf_counter() (process_time would not see the sleep)."""
    original_scan_source = civ._scan_source

    def _slow_scan_source(source: str, location: str):
        time.sleep(0.05)
        return original_scan_source(source, location)

    monkeypatch.setattr(civ, "_scan_source", _slow_scan_source)

    started = time.perf_counter()
    civ.scan_tree(ROOT, scan_roots=("scripts",))
    elapsed = time.perf_counter() - started

    with pytest.raises(AssertionError):
        assert elapsed < 0.01


# --- real-repo baseline consistency ----------------------------------------


def test_repo_baseline_file_matches_current_scan():
    """The committed baseline must reflect the real repo state -- a stale
    baseline (higher than reality) would hide new violations sneaking in
    under the registered count, silently weakening the gate."""
    baseline_path = ROOT / "idempotent-writes-baseline.json"
    baseline = civ.read_baseline(baseline_path)
    assert baseline is not None
    hits = civ.scan_tree(ROOT)
    assert len(hits) == baseline
