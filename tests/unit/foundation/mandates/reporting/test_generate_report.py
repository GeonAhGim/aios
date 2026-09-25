"""CM-16 report generation: exact fields, WORM idempotency/drift-rejection,
fail-closed negatives, failure injection, throughput, and a gate-red repro.

`scripts/replay_verify.py` (eventstore replay) is N/A(no eventstore-backed
state) for this leaf -- `report_submitter.py` is a pure `Protocol` with no
concrete adapter/migration in this task, so the determinism proof this leaf
owns is "regenerate from the same inputs -> identical `content_hash`"
(`test_regenerate_across_independent_processes_is_identical` below), the
same role `replay_verify` plays for event-sourced state.
"""

from __future__ import annotations

import os
import pickle
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.data.models.trading import OrderSide
from src.foundation.ems.contracts.v1 import RouteDecision
from src.foundation.ems.ports.route_decision_repository import RouteDecisionRecord
from src.foundation.mandates.contracts.v1 import ComplianceDecision, ComplianceVerdict
from src.foundation.mandates.reporting.application.generate_report import (
    GenerateReportInputError,
    ReportDriftError,
    generate_report,
)
from src.foundation.mandates.reporting.domain.best_execution import (
    BenchmarkKind,
    best_execution,
)
from src.foundation.mandates.reporting.domain.trade_report import normalize_trade_report
from src.foundation.mandates.reporting.ports.report_submitter import GeneratedReportRecord


class FakeReportStore:
    """In-memory `ReportSubmitterPort` -- insert-or-get keyed by `trade_id`,
    the same shape a Postgres WORM adapter must honor (no update method)."""

    def __init__(self) -> None:
        self._rows: dict[UUID, GeneratedReportRecord] = {}

    async def store(self, record: GeneratedReportRecord) -> GeneratedReportRecord:
        existing = self._rows.get(record.trade_id)
        if existing is not None:
            return existing
        self._rows[record.trade_id] = record
        return record

    async def get_by_trade_id(self, trade_id: UUID) -> GeneratedReportRecord | None:
        return self._rows.get(trade_id)


class RaisingReportStore:
    """`store()` fails like a real I/O outage -- proves `generate_report`
    does not swallow the error and fabricate a success row."""

    async def store(self, record: GeneratedReportRecord) -> GeneratedReportRecord:
        raise RuntimeError("report store unavailable")

    async def get_by_trade_id(self, trade_id: UUID) -> GeneratedReportRecord | None:
        return None


def _compliance_decision() -> ComplianceDecision:
    return ComplianceDecision(
        decision_id=uuid4(),
        verdict=ComplianceVerdict.ALLOW,
        rule_hits=[],
        inputs_hash="a" * 64,
        bundle_version="b" * 64,
        evaluated_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
    )


@pytest.fixture
def order_id() -> UUID:
    return uuid4()


@pytest.fixture
def trade_report(order_id: UUID) -> Any:
    return normalize_trade_report(
        order_id=order_id,
        trade_id=uuid4(),
        compliance_decision=_compliance_decision(),
        instrument="005930",
        venue="KRX",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("70000"),
        currency="KRW",
        executed_at=datetime(2026, 9, 9, 1, 2, 3, tzinfo=timezone.utc),
        trader_id="trader-1",
    )


@pytest.fixture
def best_execution_evidence(order_id: UUID) -> Any:
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    route = RouteDecisionRecord(
        decision_id=uuid4(),
        order_id=order_id,
        decision=RouteDecision(
            venue="KRX", reason_codes=["ONLY_VENUE"], expected_cost_bps=Decimal("5")
        ),
        candidates_snapshot=[],
        score_snapshot=[],
        weights_snapshot={},
        decided_at=now,
        created_at=now,
    )
    return best_execution(
        order_id=order_id,
        side=OrderSide.BUY,
        execution_price=Decimal("70000"),
        qty=Decimal("10"),
        benchmark=BenchmarkKind.ARRIVAL,
        route_decision=route,
        arrival_price=Decimal("69950"),
    )


@pytest.fixture
def generated_at() -> datetime:
    return datetime(2026, 9, 9, 2, 0, 0, tzinfo=timezone.utc)


async def test_builds_exact_fields_and_hash(
    trade_report: Any, best_execution_evidence: Any, generated_at: datetime
) -> None:
    record = await generate_report(
        FakeReportStore(),
        trade_report=trade_report,
        best_execution=best_execution_evidence,
        generated_at=generated_at,
    )
    assert record.trade_id == trade_report.trade_id
    assert record.order_id == trade_report.order_id
    assert record.generated_at == generated_at
    assert len(record.content_hash) == 64
    int(record.content_hash, 16)  # hex
    assert record.fields["종목코드"] == "005930"
    assert record.fields["집행_벤치마크"] == "arrival"
    assert record.fields["집행_벤치마크가격"] == "69950"
    assert record.fields["집행_사유코드"] == "ONLY_VENUE"
    assert record.fields["집행_경로판단ID"] == str(best_execution_evidence.route_decision_id)


async def test_regenerate_same_inputs_identical_hash(
    trade_report: Any, best_execution_evidence: Any, generated_at: datetime
) -> None:
    """Two independent stores (nothing shared) still compute the identical
    `content_hash` for the same domain inputs -- the hash is a pure function
    of `trade_report`/`best_execution`, not of storage state."""
    first = await generate_report(
        FakeReportStore(),
        trade_report=trade_report,
        best_execution=best_execution_evidence,
        generated_at=generated_at,
    )
    second = await generate_report(
        FakeReportStore(),
        trade_report=trade_report,
        best_execution=best_execution_evidence,
        generated_at=datetime(2099, 1, 1, tzinfo=timezone.utc),  # different clock, same inputs
    )
    assert first.content_hash == second.content_hash
    assert first.fields == second.fields


async def test_store_is_idempotent_insert_or_get(
    trade_report: Any, best_execution_evidence: Any, generated_at: datetime
) -> None:
    store = FakeReportStore()
    first = await generate_report(
        store,
        trade_report=trade_report,
        best_execution=best_execution_evidence,
        generated_at=generated_at,
    )
    second = await generate_report(
        store,
        trade_report=trade_report,
        best_execution=best_execution_evidence,
        generated_at=generated_at,
    )
    assert first == second
    assert len(store._rows) == 1


async def test_mismatched_order_rejected(
    trade_report: Any, best_execution_evidence: Any, generated_at: datetime
) -> None:
    forged = replace(best_execution_evidence, order_id=uuid4())
    with pytest.raises(GenerateReportInputError, match="does not match"):
        await generate_report(
            FakeReportStore(),
            trade_report=trade_report,
            best_execution=forged,
            generated_at=generated_at,
        )


async def test_naive_generated_at_rejected(trade_report: Any, best_execution_evidence: Any) -> None:
    with pytest.raises(GenerateReportInputError, match="tz-aware"):
        await generate_report(
            FakeReportStore(),
            trade_report=trade_report,
            best_execution=best_execution_evidence,
            generated_at=datetime(2026, 9, 9, 2, 0, 0),
        )


async def test_content_drift_rejected(
    trade_report: Any, best_execution_evidence: Any, generated_at: datetime
) -> None:
    """Adversarial / INVARIANTS.md I-04 cross-check: a stored row for this
    `trade_id` already exists with a different body (as if produced by a
    tampered or stale generator) -- `generate_report` must refuse to treat
    the mismatched row as this call's result instead of silently returning
    whatever `store()` hands back (I-04's content-hash-addressed
    immutability, applied here to a report artifact rather than a strategy
    artifact)."""
    store = FakeReportStore()
    tampered = GeneratedReportRecord(
        trade_id=trade_report.trade_id,
        order_id=trade_report.order_id,
        content_hash="0" * 64,
        fields={"tampered": "true"},
        generated_at=generated_at,
    )
    store._rows[trade_report.trade_id] = tampered
    with pytest.raises(ReportDriftError, match="different content_hash"):
        await generate_report(
            store,
            trade_report=trade_report,
            best_execution=best_execution_evidence,
            generated_at=generated_at,
        )


async def test_store_failure_propagates(
    trade_report: Any, best_execution_evidence: Any, generated_at: datetime
) -> None:
    """Failure injection: the storage port itself fails -- `generate_report`
    must not catch this and fabricate a stored-looking record (fail-closed)."""
    with pytest.raises(RuntimeError, match="report store unavailable"):
        await generate_report(
            RaisingReportStore(),
            trade_report=trade_report,
            best_execution=best_execution_evidence,
            generated_at=generated_at,
        )


@pytest.mark.perf
async def test_throughput_budget(
    trade_report: Any, best_execution_evidence: Any, generated_at: datetime
) -> None:
    """Local regression budget: 500 report generations against fresh stores
    in <2s, not a monthly SLO."""
    await generate_report(
        FakeReportStore(),
        trade_report=trade_report,
        best_execution=best_execution_evidence,
        generated_at=generated_at,
    )  # exclude first-call warmup from the measured batch
    started = perf_counter()
    results = [
        await generate_report(
            FakeReportStore(),
            trade_report=trade_report,
            best_execution=best_execution_evidence,
            generated_at=generated_at,
        )
        for _ in range(500)
    ]
    elapsed = perf_counter() - started
    assert all(result.content_hash == results[0].content_hash for result in results)
    assert elapsed < 2.0, f"500 report generations took {elapsed:.3f}s (budget 2s)"


def test_regenerate_across_independent_processes_is_identical(
    trade_report: Any, best_execution_evidence: Any, generated_at: datetime
) -> None:
    """Two fresh interpreters compute the same `content_hash` from the same
    domain inputs -- the DoD's "regenerate from the same inputs -> identical
    hash" proof, replayed outside this test's own process/hash-seed."""
    payload = pickle.dumps(
        dict(
            trade_report=trade_report,
            best_execution=best_execution_evidence,
            generated_at=generated_at,
        )
    )
    script = """
import asyncio, pickle, sys
from src.foundation.mandates.reporting.application.generate_report import generate_report

class Store:
    async def store(self, record):
        return record
    async def get_by_trade_id(self, trade_id):
        return None

case = pickle.loads(sys.stdin.buffer.read())
record = asyncio.run(generate_report(Store(), **case))
sys.stdout.buffer.write(pickle.dumps(record.content_hash))
"""
    hashes = []
    for seed in ("17", "83"):
        completed = subprocess.run(
            [sys.executable, "-c", script],
            input=payload,
            capture_output=True,
            env=dict(os.environ, PYTHONHASHSEED=seed),
            timeout=60,
            check=True,
        )
        hashes.append(pickle.loads(completed.stdout))  # noqa: S301
    assert hashes[0] == hashes[1]
    assert len(hashes[0]) == 64


def test_pytest_gate_turns_red_when_order_binding_is_removed(tmp_path: Path) -> None:
    """Run the real negative test green, then prove a bypass makes pytest exit 1."""
    test_copy = tmp_path / "test_binding.py"
    test_copy.write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\nasyncio_mode = auto\n", encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-c",
        str(config),
        "--confcutdir",
        str(tmp_path),
        f"{test_copy}::test_mismatched_order_rejected",
    ]
    env = dict(os.environ, PYTHONPATH=str(Path.cwd()), PYTEST_ADDOPTS="")
    baseline = subprocess.run(
        command, capture_output=True, text=True, env=env, timeout=60, check=False
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout
    # Mutate only the child interpreter's module; production sources stay intact.
    (tmp_path / "conftest.py").write_text(
        "import importlib\nfrom pathlib import Path\n"
        "name = 'src.foundation.mandates.reporting.application.generate_report'\n"
        "module = importlib.import_module(name)\n"
        "source = Path(module.__file__).read_text(encoding='utf-8')\n"
        "guard = 'if best_execution.order_id != trade_report.order_id:'\n"
        "assert source.count(guard) == 1\n"
        "mutant = compile(source.replace(guard, 'if False:'), module.__file__, 'exec')\n"
        "exec(mutant, module.__dict__)\n",
        encoding="utf-8",
    )
    mutated = subprocess.run(
        command, capture_output=True, text=True, env=env, timeout=60, check=False
    )
    assert mutated.returncode == 1, mutated.stdout + mutated.stderr
    assert "DID NOT RAISE" in mutated.stdout
    assert "1 failed" in mutated.stdout
