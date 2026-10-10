"""CLI baseline ratchet lifecycle -- task-10846, CONSIST-1.

Tests stay grouped by the consistency checker responsibility.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cc = _load_module("check_consistency", SCRIPTS_DIR / "check_consistency.py")


def _write(tmp_path: Path, relative: str, content: str) -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path




# ---------------------------------------------------------------------------
# main() -- 래칫 DoD
# ---------------------------------------------------------------------------


def _write_baseline(tmp_path: Path, counts: dict[str, int]) -> Path:
    path = tmp_path / "consistency-baseline.json"
    full = {m: counts.get(m, 0) for m in cc.METRICS}
    path.write_text(json.dumps(full), encoding="utf-8")
    return path


def test_main_first_run_initializes_baseline(tmp_path: Path) -> None:
    _write(tmp_path, "src/foo.py", "import datetime\nx = datetime.datetime.utcnow()\n")
    baseline_path = tmp_path / "consistency-baseline.json"

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 0
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert data["naive_datetime"] == 1
    assert set(data) == set(cc.METRICS)


def test_main_increase_fails_red(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(
        tmp_path,
        "src/foo.py",
        "import datetime\nx = datetime.datetime.utcnow()\ny = datetime.datetime.utcnow()\n",
    )
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 1})

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    out = capsys.readouterr().out
    assert exit_code == 2
    assert "naive_datetime" in out
    unchanged = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert unchanged["naive_datetime"] == 1


def test_main_decrease_without_update_keeps_baseline(tmp_path: Path) -> None:
    _write(tmp_path, "src/foo.py", "x = 1\n")
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 3})

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 0
    assert json.loads(baseline_path.read_text(encoding="utf-8"))["naive_datetime"] == 3


def test_main_decrease_with_update_ratchets_down(tmp_path: Path) -> None:
    _write(tmp_path, "src/foo.py", "x = 1\n")
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 3})

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path), "--update"])

    assert exit_code == 0
    assert json.loads(baseline_path.read_text(encoding="utf-8"))["naive_datetime"] == 0


def test_main_malformed_baseline_fails(tmp_path: Path) -> None:
    baseline_path = tmp_path / "consistency-baseline.json"
    baseline_path.write_text("not json", encoding="utf-8")
    _write(tmp_path, "src/foo.py", "x = 1\n")

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 1


def test_main_baseline_missing_metric_key_fails(tmp_path: Path) -> None:
    baseline_path = tmp_path / "consistency-baseline.json"
    baseline_path.write_text(json.dumps({"naive_datetime": 0}), encoding="utf-8")
    _write(tmp_path, "src/foo.py", "x = 1\n")

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 1
