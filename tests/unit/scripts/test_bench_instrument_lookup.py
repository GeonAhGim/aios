"""scripts/bench/instrument_lookup_bench.py 스모크 — task-6389.

`resolve_instrument`이 실DB(`md_instrument`/`md_symbol_alias`)를 거치는 벤치라
`TEST_DATABASE_URL`이 필요하다 — `tests/conftest.py`가 세션 전역에서 이미
`DATABASE_URL`로 노출해 두므로 여기서는 그 환경을 그대로 쓴다. 등록한
인스트루먼트는 트랜잭션 롤백으로 정리되므로 다른 테스트의 DB 상태를
오염시키지 않는다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.bench import instrument_lookup_bench as bench


def test_dsn_requires_test_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """negative: TEST_DATABASE_URL/DATABASE_URL 둘 다 없으면 `None`(호출자가
    fail-closed로 처리) — 조용히 인메모리로 대체하지 않는다."""
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert bench._dsn() is None


def test_measure_raises_without_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    """negative/실패 주입: DSN이 없으면 `run()`이 조용히 스킵하지 않고 실제로
    예외를 낸다 — "DB 없음=측정 생략"이 이 벤치의 목적(실DB 경로 측정)과
    모순되기 때문에 fail-closed다."""
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        bench.run(iterations=1)


@pytest.mark.skipif(
    not (os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")),
    reason="TEST_DATABASE_URL 필요",
)
def test_run_against_real_db_produces_one_sample_per_iteration() -> None:
    samples = bench.run(iterations=4)
    assert len(samples) == 4
    assert all(isinstance(s, float) and s >= 0 for s in samples)


@pytest.mark.skipif(
    not (os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")),
    reason="TEST_DATABASE_URL 필요",
)
def test_main_writes_schema_complete_passing_json(tmp_path: Path) -> None:
    out = tmp_path / "instrument_lookup_bench.json"
    rc = bench.main(["--out", str(out), "--iterations", "5"])

    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    required = {
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
    assert required <= data.keys()
    assert data["benchmark"] == "instrument_lookup_bench"
    assert data["budget_ms"] == 200.0
    assert data["passed"] is True  # 로컬 실DB는 200ms 예산을 크게 밑돈다


@pytest.mark.skipif(
    not (os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")),
    reason="TEST_DATABASE_URL 필요",
)
def test_main_records_passed_false_when_budget_is_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """적대적/실패 주입: 예산을 0ms로 조여 FAIL 경로(passed=false)가 실제로
    기록되는지 확인한다."""
    monkeypatch.setattr(bench, "BUDGET_MS", 0.0)
    out = tmp_path / "instrument_lookup_bench.json"
    rc = bench.main(["--out", str(out), "--iterations", "2"])

    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["passed"] is False
