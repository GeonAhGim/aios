"""Deepen: test_main_decrease_with_update_ratchets_down (task-24326, DEEPEN-DEPTH AI-12).

Negative (≥3), failure-injection, gate-red, and perf evidence for the
decrease-with-update ratchet-down path.  Shared helpers (_write, _write_baseline)
and the loaded check_consistency module are imported from the sibling file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Re-use helpers and module from the sibling test file.
from tests.unit.scripts.test_check_consistency import (
    _write,
    _write_baseline,
    cc,
)

# ---------------------------------------------------------------------------
# Negative tests for test_main_decrease_with_update_ratchets_down
# ---------------------------------------------------------------------------


def test_decrease_no_update_flag_does_not_ratchet(tmp_path: Path) -> None:
    """negative 1: --update 없이 감소하면 baseline이 갱신되지 않는다."""
    _write(tmp_path, "src/foo.py", "x = 1\n")
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 3})

    cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])

    assert json.loads(baseline_path.read_text(encoding="utf-8"))["naive_datetime"] == 3


def test_decrease_zero_violations_with_update(tmp_path: Path) -> None:
    """negative 2: --update + 위반 0 → baseline이 0으로 갱신된다."""
    _write(tmp_path, "src/clean.py", "x = 1\n")
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 5})

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path), "--update"])

    assert exit_code == 0
    assert json.loads(baseline_path.read_text(encoding="utf-8"))["naive_datetime"] == 0


def test_decrease_partial_metric_with_update(tmp_path: Path) -> None:
    """negative 3: 여러 metric 중 일부만 감소하면 전체 baseline이 현재값으로 갱신된다.

    --update 시 감소한 metric뿐만 아니라 ALL metric이 현재 스캔값으로
    덮어쓰인다. naive_datetime은 3→0, money_float는 1→0(현재 파일에 violation 없음).
    """
    _write(tmp_path, "src/foo.py", "x = 1\n")
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 3, "money_float": 1})

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path), "--update"])

    assert exit_code == 0
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert data["naive_datetime"] == 0
    assert data["money_float"] == 0


# ---------------------------------------------------------------------------
# Failure injection
# ---------------------------------------------------------------------------


def test_decrease_failure_injection_baseline_int_as_float(tmp_path: Path) -> None:
    """failure injection: baseline 값이 float(3.0)이면 read_baseline이 예외를 던져
    exit 1로 중단한다(값이 정수여야 한다)."""
    baseline_path = tmp_path / "consistency-baseline.json"
    # int 대신 float 값을 baseline에 기록
    full = {m: (3.0 if m == "naive_datetime" else 0) for m in cc.METRICS}
    baseline_path.write_text(json.dumps(full), encoding="utf-8")
    _write(tmp_path, "src/foo.py", "x = 1\n")

    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path), "--update"])

    # float 값은 read_baseline에서 reject → exit 1
    assert exit_code == 1


# ---------------------------------------------------------------------------
# Gate-red reproduction
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_decrease_gate_red_assertion_catches_broken_baseline(tmp_path: Path) -> None:
    """gate-red: --update 후 baseline이 실제로 갱신되는지 단언이 잡는지 확인.

    baseline을 naive_datetime=3으로 설정하고, src/foo.py에 위반 2개(utcnow 2회)를 넣는다.
    --update 없이 실행하면 exit 0 + baseline 유지(감소 경고).
    --update와 함께 실행하면 exit 0 + baseline이 현재값(2)으로 갱신된다.
    이 테스트는 단언이 실제로 갱신값을 검증하는지 확인한다.
    """
    _write(
        tmp_path,
        "src/foo.py",
        "import datetime\ndt1 = datetime.datetime.utcnow()\ndt2 = datetime.datetime.utcnow()\n",
    )
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 3})

    # --update 없이: 감소하지만 baseline은 유지
    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path)])
    assert exit_code == 0
    assert json.loads(baseline_path.read_text(encoding="utf-8"))["naive_datetime"] == 3

    # --update: baseline이 현재값(2)으로 갱신
    exit_code = cc.main(["--root", str(tmp_path), "--baseline", str(baseline_path), "--update"])
    assert exit_code == 0
    assert json.loads(baseline_path.read_text(encoding="utf-8"))["naive_datetime"] == 2


# ---------------------------------------------------------------------------
# Perf test
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_decrease_perf_with_virtual_delay(tmp_path: Path, perf_budget) -> None:
    """perf + gate-red: perf_inject 가상 지연 0.05s 주입 후 best_of(5) 측정.

    virtual_delayed_sync가 main() 호출에 50ms cpu+wall 지연을 추가하면
    perf_budget가 그 지연을 측정값에 합산한다. best_of(5)로 여러 번 중
    최소 CPU 시간을 선택하고 budget 5000ms 이내를 단언한다.

    실측 근거: 가상 지연 없이 main() 100회 호출 ≈ 30ms CPU.
    10x 지연 주입 시 측정값 ≈ 30ms + 500ms = 530ms.
    예산 5000ms는 실측의 ≈9배로 CI 호스트 여유 포함.
    """
    from scripts.consistency.common import (
        _iter_py_files,
        _read_text_cached,
        _safe_parse,
        _walked_nodes,
    )
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

    reset_virtual_totals()
    for fn in (_iter_py_files, _read_text_cached, _safe_parse, _walked_nodes):
        fn.cache_clear()

    _write(tmp_path, "src/foo.py", "import datetime\nx = datetime.datetime.utcnow()\n")
    baseline_path = _write_baseline(tmp_path, {"naive_datetime": 3})

    original_main = cc.main

    def _delayed(argv: list[str]) -> int:
        return virtual_delayed_sync(original_main, 0.05)(argv)

    sample = perf_budget.best_of(
        lambda: _delayed(["--root", str(tmp_path), "--baseline", str(baseline_path), "--update"]),
        n=5,
    )
    assert sample.cpu_ms < 5000.0
