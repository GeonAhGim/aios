"""scripts/backup/wal_archive.py 성능 테스트 -- task-25304 DEEPEN.

test_wal_archive.py(469줄)가 500줄 경계에 근접하므로 perf 단언을 sibling 파일로 분리했다
(CLAUDE.md §6.12). evaluate()와 check_archive_advanced()의 cpu_ms 예산을
PerfBudget.sample로 측정하고, virtual_delayed_sync로 게이트 적색 재현을 함께 단언한다.
"""

from __future__ import annotations

import pytest

from scripts.backup import wal_archive

# --- evaluate perf: baseline + gate-red with virtual delay -------------------


@pytest.mark.perf
def test_evaluate_performance_baseline_and_gate_red():
    """evaluate() 성능 예산 검증 — baseline 측정 + 가상 지연 게이트 적색 재현.

    성능 예산: evaluate()는 3개 dict 조회 + 문자열 비교만 하는 순수 함수이므로
    baseline cpu_ms < 0.5ms여야 한다 (ADR-2026-09-09-C Decision 1:
    스크립트/도구급은 p95 500ms 이하, evaluate()는 그보다 훨씬 작음).

    게이트 적색 재현: virtual_delayed_sync로 10ms 가상 지연 주입 →
    PerfBudget.sample.cpu_ms가 예산 0.5ms를 초과하면 적색 전환됨을 확인.
    실제 sleep/busy-wait 금지 — virtual_delayed_sync가 virtual ledger에
    나노초만 더하므로 테스트는 즉시 종료된다.
    """

    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import (
        reset_virtual_totals,
        virtual_delayed_sync,
    )

    reset_virtual_totals()

    baseline_settings = {
        "wal_level": "replica",
        "archive_mode": "on",
        "archive_command": "cp %p /archive/%f",
    }

    # baseline 측정 — 지연 없이 정상 경로
    budget = PerfBudget()
    baseline_sample = budget.sample(lambda: wal_archive.evaluate(baseline_settings))
    # baseline은 매우 빨라야 함 (순수 함수, 3개 dict 조회)
    assert baseline_sample.cpu_ms < 0.5, (
        f"baseline_perf: cpu={baseline_sample.cpu_ms:.3f}ms exceeds 0.5ms "
        f"(pure dict lookups, no delay)"
    )

    # 게이트 적색 재현: evaluate() 전체 호출에 10ms 가상 지연 주입
    reset_virtual_totals()
    delayed_evaluate = virtual_delayed_sync(lambda: wal_archive.evaluate(baseline_settings), 0.01)
    gate_red_sample = budget.sample(delayed_evaluate)
    # 10ms 지연 주입 → cpu_ms ≈ 10ms. 예산 0.5ms를 초과하므로 게이트가 적색 전환됨을 확인.
    assert gate_red_sample.cpu_ms > 0.5, (
        f"perf_gate_red: cpu={gate_red_sample.cpu_ms:.3f}ms exceeds budget 0.5ms "
        f"(10ms virtual delay injected via virtual_delayed_sync)"
    )


# --- check_archive_advanced perf: baseline + gate-red with virtual delay ------


@pytest.mark.perf
def test_check_archive_advanced_performance_baseline_and_gate_red():
    """check_archive_advanced() 성능 예산 검증 — baseline 측정 + 가상 지연 게이트 적색 재현.

    성능 예산: check_archive_advanced()는 두 set 차이 연산(set.difference)이므로
    baseline cpu_ms < 0.3ms여야 한다 (set 차이는 O(len(after)) 해시 조회).

    게이트 적색 재현: virtual_delayed_sync로 10ms 가상 지연 주입 →
    PerfBudget.sample.cpu_ms가 예산 0.3ms를 초과하면 적색 전환됨을 확인.
    """

    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import (
        reset_virtual_totals,
        virtual_delayed_sync,
    )

    reset_virtual_totals()

    before = {"000000010000000000000001", "000000010000000000000002"}
    after = {"000000010000000000000001", "000000010000000000000002"}

    # baseline 측정 — 지연 없이 정상 경로
    budget = PerfBudget()
    baseline_sample = budget.sample(lambda: wal_archive.check_archive_advanced(before, after))
    # baseline은 매우 빨라야 함 (set 차이는 O(n) 해시 조회)
    assert baseline_sample.cpu_ms < 0.3, (
        f"baseline_perf: cpu={baseline_sample.cpu_ms:.3f}ms exceeds 0.3ms "
        f"(set difference, no delay)"
    )

    # 게이트 적색 재현: check_archive_advanced() 전체 호출에 10ms 가상 지연 주입
    reset_virtual_totals()
    delayed_check = virtual_delayed_sync(
        lambda: wal_archive.check_archive_advanced(before, after), 0.01
    )
    gate_red_sample = budget.sample(delayed_check)
    # 10ms 지연 주입 → cpu_ms ≈ 10ms. 예산 0.3ms를 초과하므로 게이트가 적색 전환됨을 확인.
    assert gate_red_sample.cpu_ms > 0.3, (
        f"perf_gate_red: cpu={gate_red_sample.cpu_ms:.3f}ms exceeds budget 0.3ms "
        f"(10ms virtual delay injected via virtual_delayed_sync)"
    )


# --- virtual_delayed_sync gate-red reproduction for verify_archiving_advances --


@pytest.mark.perf
def test_verify_archiving_advances_gate_red_with_virtual_delay():
    """verify_archiving_advances() 성능 게이트 적색 재현 — virtual_delayed_sync 10ms 주입.

    verify_archiving_advances는 switch_wal/list_dir/sleep/clock을 모두 주입 가능하므로,
    list_dir에 가상 지연을 주입해 전체 호출의 cpu_ms가 예산을 초과하는지 단언한다.
    실제 대기 없이 virtual ledger만으로 게이트 적색을 재현한다.
    """

    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import (
        reset_virtual_totals,
        virtual_delayed_sync,
    )

    reset_virtual_totals()

    # list_dir에 10ms 가상 지연 주입
    call_count = {"n": 0}

    def delayed_list_dir(_d):
        call_count["n"] += 1
        return {"existing-file"} if call_count["n"] <= 3 else {"existing-file", "new-wal"}

    # virtual_delayed_sync로 list_dir에 10ms 가상 지연
    delayed_list_dir_v = virtual_delayed_sync(delayed_list_dir, 0.01)

    clock_values = iter([0.0, 0.5, 1.0, 1.5, 2.0])

    budget = PerfBudget()
    sample = budget.sample(
        lambda: wal_archive.verify_archiving_advances(
            "postgresql://x/y",
            "archive-dir",
            timeout=10.0,
            poll_interval=0.5,
            switch_wal=lambda _dsn: None,
            list_dir=delayed_list_dir_v,
            sleep=lambda _s: None,
            clock=lambda: next(clock_values),
        )
    )

    # 10ms 가상 지연 주입 → cpu_ms ≈ 10ms (list_dir가 여러 번 호출되므로 더 클 수 있음).
    # 예산 0.5ms를 초과하므로 게이트가 적색 전환됨을 확인.
    assert sample.cpu_ms > 0.5, (
        f"perf_gate_red: cpu={sample.cpu_ms:.3f}ms exceeds budget 0.5ms "
        f"(10ms virtual delay injected via virtual_delayed_sync on list_dir)"
    )
