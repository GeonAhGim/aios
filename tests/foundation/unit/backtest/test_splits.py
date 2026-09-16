"""Unit tests for `backtest/domain/splits.py` -- task-2386 L33 DoD (a)-(d)."""

import time
from typing import Literal, cast

import pytest

from src.foundation.backtest.domain import splits as splits_module
from src.foundation.backtest.domain.splits import (
    MinTrainUnsatisfiableError,
    OosLeakageError,
    Split,
    SplitError,
    assert_no_overlap,
    make_splits,
)

GAP = 15  # purge=10 + embargo=5, DoD (a) fixture


def _valid_splits(mode: Literal["anchored", "rolling"]) -> list[Split]:
    return make_splits(n_bars=1000, n_splits=5, mode=mode, purge=10, embargo=5, min_train=200)


def test_rolling_splits_have_no_leakage_and_exact_gap() -> None:
    splits = _valid_splits("rolling")
    assert len(splits) == 5
    for split in splits:
        assert set(split.train) & set(split.test) == set()
        assert split.test.start - split.train.stop == GAP


def test_anchored_splits_have_no_leakage_and_exact_gap() -> None:
    splits = _valid_splits("anchored")
    assert len(splits) == 5
    for split in splits:
        assert set(split.train) & set(split.test) == set()
        assert split.test.start - split.train.stop == GAP


def test_anchored_train_start_is_always_zero() -> None:
    splits = _valid_splits("anchored")
    assert [split.train.start for split in splits] == [0, 0, 0, 0, 0]
    assert [split.train.stop for split in splits] == [200, 357, 514, 671, 828]
    assert [split.test.start for split in splits] == [215, 372, 529, 686, 843]
    assert [split.test.stop for split in splits] == [372, 529, 686, 843, 1000]


def test_rolling_train_start_strictly_increases() -> None:
    splits = _valid_splits("rolling")
    starts = [split.train.start for split in splits]
    assert starts == [0, 157, 314, 471, 628]
    assert all(later > earlier for earlier, later in zip(starts, starts[1:], strict=False))
    assert [split.train.stop for split in splits] == [200, 357, 514, 671, 828]


def test_assert_no_overlap_accepts_valid_splits() -> None:
    assert_no_overlap(_valid_splits("rolling"))  # must not raise


def test_assert_no_overlap_rejects_overlapping_indices() -> None:
    bad = Split(train=range(0, 200), test=range(190, 300), purge=10, embargo=5)
    with pytest.raises(OosLeakageError):
        assert_no_overlap([bad])


def test_assert_no_overlap_rejects_insufficient_gap() -> None:
    bad = Split(train=range(0, 200), test=range(205, 300), purge=10, embargo=5)
    with pytest.raises(OosLeakageError):
        assert_no_overlap([bad])


def test_min_train_violation_is_rejected_not_reduced() -> None:
    with pytest.raises(MinTrainUnsatisfiableError):
        make_splits(n_bars=300, n_splits=5, mode="rolling", purge=10, embargo=5, min_train=200)


def test_min_train_violation_rejected_even_with_zero_gap() -> None:
    with pytest.raises(MinTrainUnsatisfiableError):
        make_splits(n_bars=300, n_splits=5, mode="anchored", purge=0, embargo=0, min_train=200)


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValueError):
        make_splits(
            n_bars=1000,
            n_splits=5,
            mode=cast(Literal["anchored", "rolling"], "sideways"),
            purge=10,
            embargo=5,
            min_train=200,
        )


# --- DEEPEN(task-2386): 실패 주입 -- 상류(캔들 로더) 손상 데이터도 fail-closed ---


def test_zero_bars_from_corrupted_upstream_loader_is_rejected_not_silently_empty() -> None:
    """실패 주입: 상류 캔들 로더가 손상되어(예: 빈 데이터프레임을 필터링 없이
    그대로 넘김) `n_bars=0`이 들어오면, `make_splits`는 빈 분할 리스트를 조용히
    반환하지 않고 `SplitError`로 fail-closed 거부해야 한다(CLAUDE.md §3
    "Default posture is fail-closed"). 빈 리스트를 반환하면 호출자가 "분할이
    0개였다"를 "백테스트 실행 안 함"과 구분하지 못해 조용한 데이터 누락으로
    이어진다."""
    with pytest.raises(SplitError):
        make_splits(n_bars=0, n_splits=5, mode="rolling", purge=10, embargo=5, min_train=200)


def test_second_min_train_guard_fires_when_first_guard_would_pass() -> None:
    """게이트 적색 재현 사전 조건: 기존 `test_min_train_violation_*` 두 테스트는
    모두 첫 번째 가드(`n_bars // n_splits < min_train`)만 건드린다. `gap`
    소비분까지 반영하는 두 번째 가드(`available_for_test < n_splits`)가 죽은
    코드가 아님을, 첫 번째 가드를 통과하면서 두 번째만 실패하는 입력으로
    증명한다: n_bars=7, n_splits=2, min_train=3 -> 7//2=3>=3(1차 통과), gap=3
    이므로 available_for_test=7-3-3=1 < 2(n_splits)(2차 위반)."""
    with pytest.raises(MinTrainUnsatisfiableError):
        make_splits(n_bars=7, n_splits=2, mode="rolling", purge=3, embargo=0, min_train=3)


# --- DEEPEN(task-2386): 수치 성능 단언 -- make_splits+assert_no_overlap 핫 패스 ---


def _hot_path_latencies_ms(iterations: int = 50) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        result = splits_module.make_splits(
            n_bars=100_000, n_splits=50, mode="rolling", purge=20, embargo=10, min_train=500
        )
        splits_module.assert_no_overlap(result)
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


_SPLITS_HOT_PATH_BUDGET_MS = 25.0


def test_make_splits_and_assert_no_overlap_p95_latency_within_self_declared_budget() -> None:
    """수치 성능 단언: `make_splits`+`assert_no_overlap`은 백테스트 재실행마다
    호출되는 워크포워드 경로다(ADR-2026-09-09-C 예산표에 전용 항목은 없다 --
    range 산술 + set 비교뿐인 순수 CPU 경로라는 사실 위에 자체 예산을 건다).
    n_bars=100,000/n_splits=50 규모로 로컬 실측 p95 대비 넉넉한 여유를 둔
    25ms."""
    samples = _hot_path_latencies_ms()
    p95_ms = _p95(samples)
    print(
        f"[L33 splits] make_splits+assert_no_overlap p95={p95_ms:.3f}ms "
        f"budget<{_SPLITS_HOT_PATH_BUDGET_MS:.0f}ms (n={len(samples)})"
    )
    assert p95_ms < _SPLITS_HOT_PATH_BUDGET_MS


def test_splits_budget_gate_actually_fails_past_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """게이트 적색 재현: 위 단언식이, `assert_no_overlap` 경로 한 곳이 예산을
    실제로 넘기도록 지연을 주입했을 때 진짜로 `AssertionError`를 내는지(= CI가
    실제로 빨간불이 되는지) 확인한다. 이 테스트가 없으면 위 단언이 항상
    통과하는 tautology인지 아무도 검증하지 못한다."""
    original_assert_no_overlap = splits_module.assert_no_overlap

    def _stalled_assert_no_overlap(splits: list[Split]) -> None:
        time.sleep(_SPLITS_HOT_PATH_BUDGET_MS / 1000.0)
        original_assert_no_overlap(splits)

    monkeypatch.setattr(splits_module, "assert_no_overlap", _stalled_assert_no_overlap)

    samples = _hot_path_latencies_ms(iterations=3)
    p95_ms = _p95(samples)
    with pytest.raises(AssertionError):
        assert p95_ms < _SPLITS_HOT_PATH_BUDGET_MS
