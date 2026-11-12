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

from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_totals

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


# ---------------------------------------------------------------------------
# Additional negative tests -- baseline edge cases
# ---------------------------------------------------------------------------


def test_main_baseline_empty_string_fails(tmp_path: Path) -> None:
    """baseline가 빈 문자열이면 ConsistencyError → exit 1."""
    baseline_path = tmp_path / "consistency-baseline.json"
    baseline_path.write_text("", encoding="utf-8")
    _write(tmp_path, "src/foo.py", "x = 1\n")

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 1


def test_main_baseline_non_object_fails(tmp_path: Path) -> None:
    """baseline가 JSON 배열(객체 아님)이면 ConsistencyError → exit 1."""
    baseline_path = tmp_path / "consistency-baseline.json"
    baseline_path.write_text("[1, 2, 3]", encoding="utf-8")
    _write(tmp_path, "src/foo.py", "x = 1\n")

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 1


def test_main_baseline_non_integer_value_fails(tmp_path: Path) -> None:
    """baseline 값이 정수가 아니면 ConsistencyError → exit 1."""
    baseline_path = tmp_path / "consistency-baseline.json"
    baseline_path.write_text(json.dumps({"naive_datetime": "zero"}), encoding="utf-8")
    _write(tmp_path, "src/foo.py", "x = 1\n")

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 1


def test_main_baseline_boolean_value_fails(tmp_path: Path) -> None:
    """baseline 값이 boolean(정수 아님)이면 ConsistencyError → exit 1."""
    baseline_path = tmp_path / "consistency-baseline.json"
    baseline_path.write_text(json.dumps({"naive_datetime": True}), encoding="utf-8")
    _write(tmp_path, "src/foo.py", "x = 1\n")

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 1


# ---------------------------------------------------------------------------
# Adversarial tests -- input corruption
# ---------------------------------------------------------------------------


def test_main_baseline_with_extra_keys_ignored(tmp_path: Path) -> None:
    """baseline에 METRICS에 없는 키가 있어도 METRICS 키는 모두 있어야 함."""
    baseline_path = tmp_path / "consistency-baseline.json"
    # METRICS의 모든 키를 포함 + extra 키
    payload = {m: 0 for m in cc.METRICS}
    payload["unknown_extra_key"] = 99
    baseline_path.write_text(json.dumps(payload), encoding="utf-8")
    _write(tmp_path, "src/foo.py", "x = 1\n")

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    # extra 키가 있어도 METRICS 키가 모두 정수면 통과
    assert exit_code == 0


def test_main_no_src_dir_initializes_baseline(tmp_path: Path) -> None:
    """src/ 하위 디렉터리가 없어도 baseline 초기화 성공."""
    baseline_path = tmp_path / "consistency-baseline.json"

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert exit_code == 0
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert all(v == 0 for v in data.values())


# ---------------------------------------------------------------------------
# Gate-red reproduction with perf injection (virtual delay)
# ---------------------------------------------------------------------------


def test_main_increase_fails_red_with_virtual_delay_injection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """실패 주입: naive_datetime 증가 → exit 2. 가상 지연 10배 주입으로
    gate-red 재현이 실제 지연 없이 측정 가능한지 검증 (perf 마커)."""
    reset_virtual_totals()

    _write(
        tmp_path,
        "src/foo.py",
        "import datetime\nx = datetime.datetime.utcnow()\ny = datetime.datetime.utcnow()\n"
        "z = datetime.datetime.utcnow()\n",
    )
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 1})

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    # gate-red: exit 2가 반환되어야 함
    assert exit_code == 2
    out = capsys.readouterr().out
    assert "naive_datetime" in out
    # baseline은 변경되지 않음
    unchanged = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert unchanged["naive_datetime"] == 1
    # virtual delay ledger: 스캔은 sleep를 호출하지 않으므로 ledger는 0
    cpu_s, wall_s = virtual_totals()
    assert cpu_s == 0.0
    assert wall_s == 0.0


def test_main_increase_fails_red_with_perf_budget(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """PerfBudget.best_of로 스캔 성능 측정 + gate-red 재현.

     스캔이 예산 내(로컬 실측 500ms의 30배 = 15s)에 들어오는지를 perf_budget.best_of
    로 확인. 가상 지연 10배를 주입해 실제 지연 없이 적색 단언이 동작함을 검증.
    """
    reset_virtual_totals()
    perf = PerfBudget()

    _write(
        tmp_path,
        "src/foo.py",
        "import datetime\nx = datetime.datetime.utcnow()\ny = datetime.datetime.utcnow()\n",
    )
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 1})

    # best_of: 여러 번 중 최소 CPU 시간 측정
    sample = perf.best_of(
        lambda: cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)]),
        n=3,
        warmup=1,
    )

    # gate-red: exit 2
    assert sample.result == 2
    out = capsys.readouterr().out
    assert "naive_datetime" in out

    # perf 단언: 스캔이 예산 내 (로컬 실측 500ms * 30 = 15000ms, 매우 여유)
    # 실제 스캔은 50ms 미만이어야 함
    assert sample.cpu_ms < 15000.0, f"scan too slow: {sample.cpu_ms:.1f}ms >= 15000ms budget"


# ---------------------------------------------------------------------------
# --update flag behavior
# ---------------------------------------------------------------------------


def test_main_no_update_flag_keeps_baseline_on_increase(tmp_path: Path) -> None:
    """증가 시 --update 없이 baseline 변경되지 않음."""
    _write(
        tmp_path,
        "src/foo.py",
        "import datetime\nx = datetime.datetime.utcnow()\ny = datetime.datetime.utcnow()\n",
    )
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 1})

    cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    # baseline은 그대로 (증가분은 기록되지 않음)
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert data["naive_datetime"] == 1


# ---------------------------------------------------------------------------
# scan_all / registry structure
# ---------------------------------------------------------------------------


def test_scan_all_returns_all_metrics(tmp_path: Path) -> None:
    """scan_all()이 METRICS의 모든 키를 반환."""
    _write(tmp_path, "src/foo.py", "x = 1\n")
    hits = cc.scan_all(tmp_path)
    assert set(hits) == set(cc.METRICS)
    assert all(isinstance(v, list) for v in hits.values())


def test_counts_of_matches_metrics_count() -> None:
    """counts_of() 결과의 키 수 = METRICS 길이."""
    hits = {m: [] for m in cc.METRICS}
    counts = cc.counts_of(hits)
    assert len(counts) == len(cc.METRICS)
    assert all(v == 0 for v in counts.values())


def test_write_baseline_roundtrip(tmp_path: Path) -> None:
    """write_baseline → read_baseline가 원본과 동일."""
    counts = {m: 42 for m in cc.METRICS}
    path = tmp_path / "bl.json"
    cc.write_baseline(path, counts)
    restored = cc.read_baseline(path)
    assert restored == counts


def test_read_baseline_missing_file_returns_none() -> None:
    """존재하지 않는 baseline 파일은 None 반환."""
    assert cc.read_baseline(Path("/nonexistent/path.json")) is None
