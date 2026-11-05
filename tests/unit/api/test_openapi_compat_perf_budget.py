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
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.check_openapi_compat as compat_module
from scripts.check_openapi_compat import find_violations
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

_V1_SNAPSHOT_DIFF_P95_BUDGET_MS = 300.0  # DSL 컴파일 300ms 예산 차용(모듈 docstring 참조)
_V1_SNAPSHOT_PATH = Path(__file__).resolve().parents[3] / "contracts" / "openapi" / "v1.json"


def _load_v1_snapshot() -> dict | None:
    if not _V1_SNAPSHOT_PATH.exists():
        return None  # 스냅샷 없는 환경(pytest.skip 대신 — check_code_ratchets.py skip_xfail 예산)
    return json.loads(_V1_SNAPSHOT_PATH.read_text(encoding="utf-8"))


@pytest.mark.perf
def test_find_violations_p95_latency_within_budget_for_real_v1_snapshot(
    perf_budget: PerfBudget,
) -> None:
    """CPU 예산 300ms 내 검증 — best_of로 Windows 양자화 tick 1회에 흔들리지 않게 측정."""
    snapshot = _load_v1_snapshot()
    if snapshot is None:
        return
    sample = perf_budget.best_of(
        lambda: find_violations(snapshot, snapshot), n=10, warmup=1, batch=1
    )
    assert sample.cpu_ms < _V1_SNAPSHOT_DIFF_P95_BUDGET_MS, (
        f"cpu={sample.cpu_ms:.3f}ms exceeds budget {_V1_SNAPSHOT_DIFF_P95_BUDGET_MS:.3f}ms"
    )


@pytest.mark.perf
def test_perf_budget_guard_fails_on_injected_leaf_comparison_delay(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 위 단언이 상시-녹색이 아님을 증명 — leaf 단위 비교(`_leaf_violations`,
    # 실측 v1.json 자기비교에서 3520회 호출)에 호출당 0.1ms 가상 지연을 주입하면
    # 누적 ~352ms로 같은 CPU 예산을 실제로 넘겨 AssertionError가 나야 한다.
    # virtual_delayed_sync은 실제 spin/sleep 없이 가상 레지스터에 지연만 누적하므로
    # PerfBudget.sample가 측정치에 그대로 반영한다(실제 sleep·busy-wait 금지).
    snapshot = _load_v1_snapshot()
    if snapshot is None:
        return
    reset_virtual_totals()
    original = compat_module._leaf_violations
    monkeypatch.setattr(compat_module, "_leaf_violations", virtual_delayed_sync(original, 0.0001))

    sample = perf_budget.sample(lambda: find_violations(snapshot, snapshot), batch=1)
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < _V1_SNAPSHOT_DIFF_P95_BUDGET_MS
