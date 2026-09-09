"""CM-14 exact evidence, dependency delegation and fail-closed tests."""
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
from uuid import uuid4

import pytest

from src.data.models.market_data import Candle
from src.data.models.trading import OrderSide
from src.foundation.ems.contracts.v1 import RouteDecision
from src.foundation.ems.domain.tca import benchmarks
from src.foundation.ems.ports.route_decision_repository import RouteDecisionRecord
from src.foundation.mandates.reporting.domain.best_execution import (
    BenchmarkKind,
    BestExecutionInputMissing,
    best_execution,
)


@pytest.fixture
def inputs() -> dict[str, Any]:
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    order_id = uuid4()
    route = RouteDecisionRecord(
        decision_id=uuid4(), order_id=order_id,
        decision=RouteDecision(venue="test", reason_codes=["ONLY_VENUE", "CUSTOM_REASON"],
                               expected_cost_bps=Decimal("999")),
        candidates_snapshot=[], score_snapshot=[], weights_snapshot={},
        decided_at=now, created_at=now,
    )
    bar = Candle(symbol="BTC-USD", exchange="test", timeframe="1m",
                 open=Decimal("100"), high=Decimal("100"), low=Decimal("100"),
                 close=Decimal("100"), volume=Decimal("1"), open_time=now, close_time=now)
    return dict(order_id=order_id, side=OrderSide.BUY, execution_price=Decimal("100.5"),
                qty=Decimal("10"), benchmark=BenchmarkKind.ARRIVAL, route_decision=route,
                arrival_price=Decimal("100.0"), bars=[bar],
                benchmark_fills=[benchmarks.Fill(Decimal("100"), Decimal("10"))])


@pytest.mark.parametrize("side,expected", [(OrderSide.BUY, "50"), (OrderSide.SELL, "-50")])
def test_exact_slippage(inputs: dict[str, Any], side: OrderSide, expected: str) -> None:
    inputs["side"] = side
    result = best_execution(**inputs)
    assert result.slippage_bps == Decimal(expected)
    assert isinstance(result.slippage_bps, Decimal)
    assert result.qty == Decimal("10")
    assert result.route_decision_id == inputs["route_decision"].decision_id
    assert result.reason_codes == ("ONLY_VENUE", "CUSTOM_REASON")
    assert result == best_execution(**inputs)
    inputs["route_decision"].decision.reason_codes.append("LATER_MUTATION")
    assert result.reason_codes == ("ONLY_VENUE", "CUSTOM_REASON")


@pytest.mark.parametrize("kind,function", [
    (BenchmarkKind.ARRIVAL, "arrival_price"), (BenchmarkKind.VWAP, "compute_vwap"),
    (BenchmarkKind.CLOSE, "close_price"),
])
def test_delegation_failure_propagates(
    inputs: dict[str, Any], monkeypatch: pytest.MonkeyPatch, kind: BenchmarkKind, function: str,
) -> None:
    def fail(*args: object) -> Decimal:
        raise RuntimeError("EM-12 called")
    inputs["benchmark"] = kind
    monkeypatch.setattr(benchmarks, function, fail)
    with pytest.raises(RuntimeError, match="EM-12 called"):
        best_execution(**inputs)


@pytest.mark.parametrize("kind,field,value,code", [
    (BenchmarkKind.ARRIVAL, "arrival_price", None, "ARRIVAL_PRICE_MISSING"),
    (BenchmarkKind.VWAP, "benchmark_fills", [], "BENCHMARK_FILLS_MISSING"),
    (BenchmarkKind.CLOSE, "bars", [], "BENCHMARK_BARS_MISSING"),
])
def test_missing_benchmark(inputs: dict[str, Any], kind: BenchmarkKind,
                           field: str, value: object, code: str) -> None:
    inputs.update(benchmark=kind)
    inputs[field] = value
    with pytest.raises(BestExecutionInputMissing) as error:
        best_execution(**inputs)
    assert error.value.code == code


@pytest.mark.parametrize("reasons", [None, [], [""], [" "]])
def test_missing_route(inputs: dict[str, Any], reasons: list[str] | None) -> None:
    if reasons is None:
        inputs["route_decision"] = None
    else:
        inputs["route_decision"].decision.reason_codes = reasons
    with pytest.raises(BestExecutionInputMissing, match="ROUTE_DECISION_MISSING"):
        best_execution(**inputs)


def test_wrong_order(inputs: dict[str, Any]) -> None:
    inputs["route_decision"] = replace(inputs["route_decision"], order_id=uuid4())
    with pytest.raises(ValueError, match="another order"):
        best_execution(**inputs)


@pytest.mark.parametrize("field", ["execution_price", "qty", "arrival_price"])
@pytest.mark.parametrize("value", [100.5, Decimal("0"), Decimal("-1"),
                                  Decimal("NaN"), Decimal("Infinity")])
def test_invalid_numbers(inputs: dict[str, Any], field: str, value: object) -> None:
    inputs[field] = value
    with pytest.raises(ValueError, match="finite positive Decimal"):
        best_execution(**inputs)


@pytest.mark.parametrize("kind", [BenchmarkKind.VWAP, BenchmarkKind.CLOSE])
def test_other_benchmarks(inputs: dict[str, Any], kind: BenchmarkKind) -> None:
    inputs["benchmark"] = kind
    assert best_execution(**inputs).slippage_bps == Decimal("50")


@pytest.mark.parametrize("kind,function", [
    (BenchmarkKind.ARRIVAL, "arrival_price"), (BenchmarkKind.VWAP, "compute_vwap"),
    (BenchmarkKind.CLOSE, "close_price"),
])
@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("0"),
                                  Decimal("-1"), 100.0])
def test_corrupt_dependency_cannot_emit_evidence(
    inputs: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
    kind: BenchmarkKind, function: str, value: object,
) -> None:
    inputs["benchmark"] = kind
    monkeypatch.setattr(benchmarks, function, lambda *_: value)
    with pytest.raises(ValueError, match="benchmark_price must be a finite positive Decimal"):
        best_execution(**inputs)


@pytest.mark.parametrize("side,expected", [(OrderSide.BUY, "100"), (OrderSide.SELL, "-100")])
def test_weighted_window_exact_cost_and_performance(
    inputs: dict[str, Any], side: OrderSide, expected: str,
) -> None:
    """Local regression budget: 100 reports of 1,000 samples in <2s, not a monthly SLO."""
    inputs.update(benchmark=BenchmarkKind.VWAP, side=side, execution_price=Decimal("108.575"),
                  benchmark_fills=[benchmarks.Fill(Decimal("100"), Decimal("1")),
                                   benchmarks.Fill(Decimal("110"), Decimal("3"))] * 500)
    best_execution(**inputs)  # Exclude first-call setup from the measured batch.
    started = perf_counter()
    results = [best_execution(**inputs) for _ in range(100)]
    elapsed = perf_counter() - started
    assert all(result.benchmark_price == Decimal("107.5") for result in results)
    assert all(result.slippage_bps == Decimal(expected) for result in results)
    assert elapsed < 2.0, f"100 x 1,000-sample reports took {elapsed:.3f}s (budget 2s)"


def test_independent_process_replay_and_order_isolation(inputs: dict[str, Any]) -> None:
    """Two fresh interpreters replay identical snapshots, including after a forged route."""
    cases = []
    for kind in BenchmarkKind:
        for side in OrderSide:
            order_id = uuid4()
            cases.append(dict(inputs, benchmark=kind, side=side, order_id=order_id,
                              route_decision=replace(inputs["route_decision"], order_id=order_id,
                                                     decision_id=uuid4())))
    expected = [best_execution(**case) for case in cases]
    payload = pickle.dumps(cases)
    script = """
import pickle, sys
from src.foundation.mandates.reporting.domain.best_execution import best_execution
cases = pickle.loads(sys.stdin.buffer.read())
first = [best_execution(**case) for case in cases]
forged = dict(cases[0], route_decision=cases[-1]['route_decision'])
try:
    best_execution(**forged)
except ValueError as error:
    assert 'another order' in str(error)
else:
    raise AssertionError('forged route accepted')
replayed = [best_execution(**case) for case in reversed(cases)]
assert first == list(reversed(replayed))
sys.stdout.buffer.write(pickle.dumps(first))
"""
    outputs = []
    for seed in ("17", "83"):
        completed = subprocess.run(
            [sys.executable, "-c", script], input=payload, capture_output=True,
            env=dict(os.environ, PYTHONHASHSEED=seed), timeout=60, check=True,
        )
        outputs.append(completed.stdout)
        # Only this test's own child process produces the serialized evidence.
        assert pickle.loads(completed.stdout) == expected  # noqa: S301
    assert outputs[0] == outputs[1]
    assert len({result.order_id for result in expected}) == len(cases)
    assert len({result.route_decision_id for result in expected}) == len(cases)


def test_pytest_gate_turns_red_when_order_binding_is_removed(tmp_path: Path) -> None:
    """Run the real negative test green, then prove a bypass makes pytest exit 1."""
    test_copy = tmp_path / "test_binding.py"
    test_copy.write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
               "-c", str(config), "--confcutdir", str(tmp_path),
               f"{test_copy}::test_wrong_order"]
    env = dict(os.environ, PYTHONPATH=str(Path.cwd()), PYTEST_ADDOPTS="")
    baseline = subprocess.run(command, capture_output=True, text=True, env=env,
                              timeout=60, check=False)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout
    # Mutate only the child interpreter's module; production sources stay intact.
    (tmp_path / "conftest.py").write_text(
        "import importlib\nfrom pathlib import Path\n"
        "name = 'src.foundation.mandates.reporting.domain.best_execution'\n"
        "module = importlib.import_module(name)\n"
        "source = Path(module.__file__).read_text(encoding='utf-8')\n"
        "guard = 'if route_decision.order_id != order_id:'\n"
        "assert source.count(guard) == 1\n"
        "mutant = compile(source.replace(guard, 'if False:'), module.__file__, 'exec')\n"
        "exec(mutant, module.__dict__)\n",
        encoding="utf-8",
    )
    mutated = subprocess.run(command, capture_output=True, text=True, env=env,
                             timeout=60, check=False)
    assert mutated.returncode == 1, mutated.stdout + mutated.stderr
    assert "DID NOT RAISE" in mutated.stdout
    assert "1 failed" in mutated.stdout
