"""Unit tests for `backtest/domain/param_stability.py` -- task-2386 L33 DoD (e)."""

from __future__ import annotations

import time
from decimal import Decimal
from itertools import product

import pytest

from src.foundation.backtest.domain import param_stability as param_stability_module
from src.foundation.backtest.domain.param_stability import (
    ParamGrid,
    ParamStabilityError,
    stability_score,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

AXES = {"p": [5, 10, 15, 20, 25]}


def test_sharp_peak_is_isolated() -> None:
    grid = ParamGrid(axes=AXES)
    metric_by_point = {
        (5,): Decimal("0.05"),
        (10,): Decimal("0.1"),
        (15,): Decimal("1.0"),
        (20,): Decimal("0.1"),
        (25,): Decimal("0.05"),
    }
    report = stability_score(grid, metric_by_point)
    assert report.best == (15,)
    assert report.neighbor_mean == Decimal("0.1")
    assert report.isolated is True


def test_gentle_plateau_is_not_isolated() -> None:
    grid = ParamGrid(axes=AXES)
    metric_by_point = {
        (5,): Decimal("0.8"),
        (10,): Decimal("0.9"),
        (15,): Decimal("1.0"),
        (20,): Decimal("0.9"),
        (25,): Decimal("0.8"),
    }
    report = stability_score(grid, metric_by_point)
    assert report.best == (15,)
    assert report.neighbor_mean == Decimal("0.9")
    assert report.isolated is False


def test_grid_smaller_than_four_hard_fails() -> None:
    with pytest.raises(ParamStabilityError):
        ParamGrid(axes={"p": [5, 10]})


def test_missing_metric_point_is_rejected() -> None:
    grid = ParamGrid(axes=AXES)
    incomplete = {(5,): Decimal("0.1"), (10,): Decimal("0.1"), (15,): Decimal("1.0")}
    with pytest.raises(ParamStabilityError):
        stability_score(grid, incomplete)


def test_non_ascending_axis_is_rejected() -> None:
    with pytest.raises(ParamStabilityError):
        ParamGrid(axes={"p": [5, 20, 10, 15, 25]})


# --- DEEPEN(task-2386): 실패 주입 -- 상류(백테스트 리포트) 손상 데이터도 fail-closed ---


def test_non_decimal_metric_from_corrupted_upstream_report_raises_not_silently_wrong() -> None:
    """실패 주입: 상류 백테스트 리포트 직렬화가 손상되어(CLAUDE.md §3 "Monetary
    amounts are Decimal, never float" 위반 -- 예: JSON round-trip에서 Decimal이
    float로 뭉개짐) `metric_by_point`에 `Decimal` 대신 `float`가 하나 섞여
    들어오면, `stability_score`는 그 값을 조용히 섞어 잘못된 점수를 내지 않고
    예외로 fail-closed 거부해야 한다. `Decimal + float` 산술은 CPython에서
    `TypeError`를 내므로, 이 테스트는 그 사실이 실제로 유지되는지(= 향후
    리팩터링이 어딘가에서 `float(...)` 캐스팅을 넣어 자동 형변환을 조용히
    허용하지 않는지) 못박는다."""
    grid = ParamGrid(axes=AXES)
    good_metric_by_point = {
        (5,): Decimal("0.05"),
        (10,): Decimal("0.1"),
        (15,): Decimal("1.0"),
        (20,): Decimal("0.1"),
        (25,): Decimal("0.05"),
    }
    corrupted = {**good_metric_by_point, (20,): 0.1}  # 주입된 손상: float, Decimal 아님
    with pytest.raises(TypeError):
        stability_score(grid, corrupted)


# --- DEEPEN(task-2386): 수치 성능 단언 -- stability_score 핫 패스 ---


def _stability_score_latencies_ms(iterations: int = 30) -> list[float]:
    axes = {"a": list(range(8)), "b": list(range(8)), "c": list(range(8))}
    grid = ParamGrid(axes=axes)
    points = list(product(*axes.values()))
    metric_by_point = {point: Decimal(str(1.0 - 0.01 * sum(point))) for point in points}
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        param_stability_module.stability_score(grid, metric_by_point)
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


_STABILITY_SCORE_BUDGET_MS = 25.0


@pytest.mark.perf
def test_stability_score_p95_latency_within_self_declared_budget() -> None:
    """수치 성능 단언: `stability_score`는 그리드 서치 후 최적점 하나를 고를 때
    호출되는 경로다(ADR-2026-09-09-C 예산표에 전용 항목은 없다 -- 512점 그리드
    순회 + Decimal 산술뿐인 순수 CPU 경로라는 사실 위에 자체 예산을 건다).
    8x8x8=512점 그리드 기준 로컬 실측 p95 대비 넉넉한 여유를 둔 25ms."""
    samples = _stability_score_latencies_ms()
    p95_ms = _p95(samples)
    print(
        f"[L33 param_stability] stability_score p95={p95_ms:.3f}ms "
        f"budget<{_STABILITY_SCORE_BUDGET_MS:.0f}ms (n={len(samples)})"
    )
    assert p95_ms < _STABILITY_SCORE_BUDGET_MS


@pytest.mark.perf
def test_stability_score_best_of_latency_within_budget() -> None:
    """성능 단언 (D2 보강): `PerfBudget.best_of`로 `stability_score`의 CPU 시간을
    측정한다. best_of는 여러 번 중 최소를 선택해 노이즈를 걷어낸다.
    8x8x8=512점 그리드 기준 로컬 실측 best_of(5) cpu_ms 대비 25ms 예산."""
    axes = {"a": list(range(8)), "b": list(range(8)), "c": list(range(8))}
    grid = ParamGrid(axes=axes)
    points = list(product(*axes.values()))
    metric_by_point = {point: Decimal(str(1.0 - 0.01 * sum(point))) for point in points}

    def _call() -> object:
        return stability_score(grid, metric_by_point)

    pb = PerfBudget()
    sample = pb.best_of(_call, n=5, warmup=1)
    budget_ms = 25.0
    print(
        f"[L33 param_stability] stability_score best_of(5) cpu={sample.cpu_ms:.3f}ms "
        f"budget<{budget_ms:.0f}ms"
    )
    assert sample.cpu_ms < budget_ms


@pytest.mark.perf
def test_stability_score_budget_gate_actually_fails_past_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현: `virtual_delayed_sync`로 `ParamGrid.neighbors`에
    가상 지연(예산 10배)을 주입하면 `PerfBudget` 측정치가 예산을 넘어
    `AssertionError`를 낸다. 실제 sleep/busy-wait 없이 virtual ledger만
    누수하므로 테스트 실행 시간은 증가하지 않는다."""
    reset_virtual_totals()
    injected_delay_s = _STABILITY_SCORE_BUDGET_MS * 10 / 1000.0  # 10x 가상 지연

    original_neighbors = ParamGrid.neighbors
    delayed_neighbors = virtual_delayed_sync(original_neighbors, injected_delay_s)
    monkeypatch.setattr(ParamGrid, "neighbors", delayed_neighbors)

    axes = {"a": list(range(8)), "b": list(range(8)), "c": list(range(8))}
    grid = ParamGrid(axes=axes)
    points = list(product(*axes.values()))
    metric_by_point = {point: Decimal(str(1.0 - 0.01 * sum(point))) for point in points}

    pb = PerfBudget()
    sample = pb.best_of(lambda: stability_score(grid, metric_by_point), n=3, warmup=1)
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < _STABILITY_SCORE_BUDGET_MS
