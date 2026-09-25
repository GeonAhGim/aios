"""Unit tests for `backtest/domain/param_stability.py` -- task-2386 L33 DoD (e)."""

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


def test_stability_score_budget_gate_actually_fails_past_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현: 위 단언식이, `ParamGrid.neighbors` 경로 한 곳이 예산을
    실제로 넘기도록 지연을 주입했을 때 진짜로 `AssertionError`를 내는지(= CI가
    실제로 빨간불이 되는지) 확인한다. 이 테스트가 없으면 위 단언이 항상
    통과하는 tautology인지 아무도 검증하지 못한다."""
    original_neighbors = ParamGrid.neighbors

    def _stalled_neighbors(self: ParamGrid, point: tuple[int, ...]) -> list[tuple[int, ...]]:
        time.sleep(_STABILITY_SCORE_BUDGET_MS / 1000.0)
        return original_neighbors(self, point)

    monkeypatch.setattr(ParamGrid, "neighbors", _stalled_neighbors)

    samples = _stability_score_latencies_ms(iterations=3)
    p95_ms = _p95(samples)
    with pytest.raises(AssertionError):
        assert p95_ms < _STABILITY_SCORE_BUDGET_MS
