"""scripts/bench/backtest_instant_bench.py 스모크 — task-6389.

순수(no I/O) `run_quick_backtest` 위에서 인메모리 1년 일봉을 도는 벤치라
DB/네트워크 없이 단위테스트로 검증한다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.bench import backtest_instant_bench as bench

_REQUIRED_KEYS = {
    "benchmark",
    "generated_at",
    "git_sha",
    "machine",
    "budget_ms",
    "samples",
    "p50_ms",
    "p95_ms",
    "max_ms",
    "passed",
}


def test_one_year_daily_columns_has_365_bars() -> None:
    columns = bench._one_year_daily_columns()
    assert len(columns.close) == bench.BARS_PER_YEAR == 365


def test_one_year_daily_columns_is_deterministic() -> None:
    """negative/결정론: 같은 seed면 같은 종가열 — 벤치 입력이 실행마다 흔들리면
    p50/p95 비교가 무의미해진다."""
    a = bench._one_year_daily_columns()
    b = bench._one_year_daily_columns()
    assert a.close == b.close


def test_run_produces_one_sample_per_iteration() -> None:
    samples = bench.run(iterations=3)
    assert len(samples) == 3
    assert all(isinstance(s, float) and s >= 0 for s in samples)


def test_main_writes_schema_complete_passing_json(tmp_path: Path) -> None:
    out = tmp_path / "backtest_instant_bench.json"
    rc = bench.main(["--out", str(out), "--iterations", "3"])

    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert _REQUIRED_KEYS <= data.keys()
    assert data["benchmark"] == "backtest_instant_bench"
    assert data["budget_ms"] == 5_000.0
    assert data["passed"] is True  # 인메모리 365봉은 5s 예산을 크게 밑돈다


def test_main_records_passed_false_when_budget_is_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """적대적/실패 주입: 예산을 0ms로 조여 FAIL 경로(passed=false)가 실제로
    기록되는지 — PASS 단언이 동어반복이 아님을 증명한다."""
    monkeypatch.setattr(bench, "BUDGET_MS", 0.0)
    out = tmp_path / "backtest_instant_bench.json"
    rc = bench.main(["--out", str(out), "--iterations", "2"])

    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["passed"] is False


def test_main_default_output_path_is_docs_perf() -> None:
    assert bench.DEFAULT_OUT.parts[-3:] == ("docs", "perf", "backtest_instant_bench.json")
