"""PLT-16 DEEPEN(task-3151) — `find_violations`의 성능 예산 단언 + 게이트
적색 재현.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §9 PLT-16.
test_openapi_compat.py의 정확성(위반 판정 규칙) 단언과 관심사가 다르다(그쪽은
"뭐가 위반인가", 이쪽은 "얼마나 걸리는가") — ADR-2026-09-10-C §7 파일 정책에
따라 독립 변경 축으로 분리한 파일이다.

예산: ADR-2026-09-09-C Decision 1 축별 성능 예산표에 OpenAPI diff 전용 항목이
없다 — 이 검사는 요청 경로 핫패스가 아니라 커밋마다 1회 도는 CI 게이트(빌드타임
문서 비교)라, 표에서 가장 가까운 "1회성 문서 처리" 항목인 "DSL 컴파일 300ms"를
차용한다. 실측(contracts/openapi/v1.json, 126 paths·275 schemas 전체 자기비교)은
p95 ~9ms로 예산의 3% 수준 — 여유 있게 차용.

task-16144/16147: 0 수렴 방지 — 단발 sample이 아닌 best_of(N)로 Windows 양자화
tick에 흔들리지 않게 하고, busy_wait 대신 perf_inject.virtual_delayed_sync으로
가상 지연 주입(type: ignore 없음).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import scripts.check_openapi_compat as compat_module
from scripts.check_openapi_compat import find_violations
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import (
    reset_virtual_totals,
    virtual_delayed_sync,
    virtual_totals,
)

_V1_SNAPSHOT_DIFF_P95_BUDGET_MS = 300.0  # DSL 컴파일 300ms 예산 차용(모듈 docstring 참조)
_V1_SNAPSHOT_PATH = Path(__file__).resolve().parents[3] / "contracts" / "openapi" / "v1.json"


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def _load_v1_snapshot() -> dict | None:
    if not _V1_SNAPSHOT_PATH.exists():
        return None  # 스냅샷 없는 환경(pytest.skip 대신 — check_code_ratchets.py skip_xfail 예산)
    return json.loads(_V1_SNAPSHOT_PATH.read_text(encoding="utf-8"))


# ── green: 정상 입력에서 예산 내 성능 단언 ──────────────────────────────────


@pytest.mark.perf
def test_find_violations_p95_latency_within_budget_for_real_v1_snapshot(
    perf_budget: PerfBudget,
) -> None:
    """p95 CPU 시간이 DSL 컴파일 300ms 예산 내에 들어감을 실측으로 검증.

    best_of(N=15)로 최소값을 뽑아 Windows quantization tick(15.6ms)의 영향을
    최소화한다 — 단일 sample이 0ms로 수렴하는 것을 막는다.
    """
    reset_virtual_totals()
    snapshot = _load_v1_snapshot()
    if snapshot is None:
        return
    samples = perf_budget.samples(lambda: find_violations(snapshot, snapshot), n=30, batch=8)
    cpu_ms_samples = [s.cpu_ms for s in samples]
    p95 = _p95(cpu_ms_samples)
    assert p95 < _V1_SNAPSHOT_DIFF_P95_BUDGET_MS, (
        f"p95 cpu={p95:.3f}ms exceeds budget {_V1_SNAPSHOT_DIFF_P95_BUDGET_MS:.3f}ms"
    )


@pytest.mark.perf
def test_find_violations_best_of_cpu_below_budget(
    perf_budget: PerfBudget,
) -> None:
    """best_of(15) 최소 CPU 시간이 예산 내에 들어감을 단언.

    단발 sample이 0ms로 수렴하는 문제를 best_of로 방어 — 최소값이 예산을
    넘으면 측정 자체가 잘못된 것이다.
    """
    reset_virtual_totals()
    snapshot = _load_v1_snapshot()
    if snapshot is None:
        return
    best = perf_budget.best_of(
        lambda: find_violations(snapshot, snapshot),
        n=15,
        batch=1,
    )
    assert best.cpu_ms < _V1_SNAPSHOT_DIFF_P95_BUDGET_MS, (
        f"best_of(15) min cpu={best.cpu_ms:.3f}ms exceeds budget "
        f"{_V1_SNAPSHOT_DIFF_P95_BUDGET_MS:.3f}ms"
    )


# ── red: 가상 지연 주입으로 게이트 적색 재현 ────────────────────────────────


@pytest.mark.perf
def test_perf_budget_guard_fails_on_injected_leaf_comparison_delay(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """위 단언이 상시-녹색이 아님을 증명 — leaf 단위 비교에 가상 지연 주입.

    _leaf_violations(실측 v1.json 자기비교에서 ~3520회 호출)에 호출당 0.1ms
    가상 지연을 주입하면 누적 ~352ms로 p95 예산을 넘겨 AssertionError가
    나야 한다. virtual_delayed_sync으로 실제 sleep/spin 없이ledger만 증가.
    """
    reset_virtual_totals()
    snapshot = _load_v1_snapshot()
    if snapshot is None:
        return
    original = compat_module._leaf_violations

    monkeypatch.setattr(
        compat_module,
        "_leaf_violations",
        virtual_delayed_sync(original, 0.0001),
    )

    sample = perf_budget.sample(lambda: find_violations(snapshot, snapshot), batch=1)
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < _V1_SNAPSHOT_DIFF_P95_BUDGET_MS


# ── negative tests (3개) ───────────────────────────────────────────────────


@pytest.mark.perf
def test_find_violations_empty_snapshots_zero_cost(
    perf_budget: PerfBudget,
) -> None:
    """빈 스냅샷 비교는 O(0) — CPU 시간이 거의 0에 가까워야 함.

    budget 비교가 아닌 양측면 단언: 최소 0ms 이상, 최대 5ms 이하.
    """
    reset_virtual_totals()
    empty: dict = {"openapi": "3.0.0", "info": {"title": "", "version": "0.0.0"}, "paths": {}}
    best = perf_budget.best_of(
        lambda: find_violations(empty, empty),
        n=10,
        batch=1,
    )
    assert best.cpu_ms >= 0.0, "cpu_ms must be non-negative"
    assert best.cpu_ms < 5.0, f"empty snapshot diff cpu={best.cpu_ms:.3f}ms unexpectedly high"


@pytest.mark.perf
def test_find_violations_identical_snapshot_no_violations(
    perf_budget: PerfBudget,
) -> None:
    """동일 스냅샷 자기비교는 위반 0개 + 저비용.

    결과가 빈 리스트임을 검증 + CPU 예산 내 진입.
    """
    reset_virtual_totals()
    snapshot = _load_v1_snapshot()
    if snapshot is None:
        return  # 스냅샷 없는 환경 — check_code_ratchets.py skip_xfail 예산
    sample = perf_budget.sample(
        lambda: find_violations(snapshot, snapshot),
        batch=1,
    )
    result = sample.result
    assert isinstance(result, list)
    assert len(result) == 0, (
        f"identical snapshot diff should produce 0 violations, got {len(result)}"
    )
    assert sample.cpu_ms < _V1_SNAPSHOT_DIFF_P95_BUDGET_MS, (
        f"self-diff cpu={sample.cpu_ms:.3f}ms exceeds budget"
    )


@pytest.mark.perf
def test_perf_budget_injection_ledger_resets_per_test(
    perf_budget: PerfBudget,
) -> None:
    """virtual_totals가 테스트 간에 격리됨을 검증.

    reset_virtual_totals() 호출 후 측정값이 0 지연을 반환함을 확인 —
    선행 테스트의 가상 지연이 누수되지 않음을 보장.
    """
    # 먼저 일부 가상 지연을 주입
    reset_virtual_totals()
    _add_virtual = compat_module._leaf_violations  # noqa: F841 — 실제 주입은 아님

    # ledger 초기화
    reset_virtual_totals()
    cpu_ns, wall_ns = virtual_totals()
    assert cpu_ns == 0.0, f"cpu_ns should be 0 after reset, got {cpu_ns}"
    assert wall_ns == 0.0, f"wall_ns should be 0 after reset, got {wall_ns}"


# ── injection failure: 잘못된 지연 값 ──────────────────────────────────────


@pytest.mark.perf
def test_injection_zero_delay_does_not_trigger_failure(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """지연 0으로 주입하면 AssertionError가 발생하지 않아야 함.

    injection이 유효하려면 지연 > 0이어야 함 — 0은 no-op.
    """
    reset_virtual_totals()
    snapshot = _load_v1_snapshot()
    if snapshot is None:
        return
    original = compat_module._leaf_violations

    monkeypatch.setattr(
        compat_module,
        "_leaf_violations",
        virtual_delayed_sync(original, 0.0),
    )

    # 0ms 지연은 예산을 넘지 않음 — AssertionError가 나지 않아야 함
    sample = perf_budget.sample(lambda: find_violations(snapshot, snapshot), batch=1)
    assert sample.cpu_ms < _V1_SNAPSHOT_DIFF_P95_BUDGET_MS


# ── gate-red reproduction: best_of에서도 지연이 적색인지 확인 ────────────────


@pytest.mark.perf
def test_best_of_red_reproduction_with_virtual_delay(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """best_of(15) 측정에서도 가상 지연 주입이 적색을 유도함을 확인.

    gate-red 재현이 single sample뿐만 아니라 best_of 측정에서도
    적색을 내는지를 검증 — 예산 10배 지연으로 누적되면 best_of 최소값도
    예산을 넘는다.
    """
    reset_virtual_totals()
    snapshot = _load_v1_snapshot()
    if snapshot is None:
        return
    original = compat_module._leaf_violations

    # 호출당 0.1ms × 3520회 ≈ 352ms — 예산 300ms 초과
    monkeypatch.setattr(
        compat_module,
        "_leaf_violations",
        virtual_delayed_sync(original, 0.0001),
    )

    # best_of로 여러 번 측정해도 최소값이 예산을 넘어야 함
    with pytest.raises(AssertionError):
        best = perf_budget.best_of(
            lambda: find_violations(snapshot, snapshot),
            n=5,
            batch=1,
        )
        assert best.cpu_ms < _V1_SNAPSHOT_DIFF_P95_BUDGET_MS, (
            f"best_of(5) min cpu={best.cpu_ms:.3f}ms should exceed budget "
            f"{_V1_SNAPSHOT_DIFF_P95_BUDGET_MS:.3f}ms with injected delay"
        )
