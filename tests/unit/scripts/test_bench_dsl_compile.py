"""scripts/bench/dsl_compile_bench.py 스모크 — task-6389.

DB/네트워크 없이 순수 함수라 단위테스트로 검증한다. `run()`이 실제 ms 표본을
내는지, `main()`이 스키마(§ 명세: benchmark/generated_at/git_sha/machine/
budget_ms/samples/p50_ms/p95_ms/max_ms/passed)를 지키는 JSON을 쓰는지, 예산을
초과하면 `passed=false`로 fail-closed 기록되는지(적색 재현) 확인한다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.bench import dsl_compile_bench as bench

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


def test_run_produces_one_sample_per_script_per_iteration() -> None:
    samples = bench.run(iterations=2)
    assert len(samples) == len(bench.SAMPLE_SCRIPTS) * 2
    assert all(isinstance(s, float) and s >= 0 for s in samples)


def test_main_writes_schema_complete_passing_json(tmp_path: Path) -> None:
    out = tmp_path / "dsl_compile_bench.json"
    rc = bench.main(["--out", str(out), "--iterations", "3"])

    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert _REQUIRED_KEYS <= data.keys()
    assert data["benchmark"] == "dsl_compile_bench"
    assert data["budget_ms"] == 300.0
    assert data["passed"] is True  # 실장비에서 300ms 예산을 크게 밑돈다


def test_main_records_passed_false_when_budget_is_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """적대적/실패 주입: 예산을 0ms로 조여 실제로 FAIL 경로(passed=false)가
    기록되는지 — 위 PASS 단언이 항상 참인 동어반복이 아님을 증명한다."""
    monkeypatch.setattr(bench, "BUDGET_MS", 0.0)  # type: ignore[attr-defined]
    out = tmp_path / "dsl_compile_bench.json"
    rc = bench.main(["--out", str(out), "--iterations", "2"])

    assert rc == 0  # 예산 초과여도 스크립트 자체는 0으로 끝난다 — 게이트는 JSON의 passed로 판정
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["passed"] is False


def test_main_default_output_path_is_docs_perf() -> None:
    """negative: --out을 생략하면 저장소 표준 경로(docs/perf/)로 떨어져야 한다."""
    assert bench.DEFAULT_OUT.parts[-3:] == ("docs", "perf", "dsl_compile_bench.json")
