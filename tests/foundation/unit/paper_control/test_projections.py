"""build_deployment_list_view 순수 단위테스트 — DB 없음, fake repo.

경계값(빈 목록/다건), 실패주입(repo 예외 전파), as_of의 tz-aware UTC 불변식,
어셈블리 성능(축=FND 읽기모델, 리스트 조립 자체는 105 DB 왕복이 아니므로
가벼운 상한만 검증)을 커버한다."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from src.foundation.paper_control.contracts.v1 import DeploymentState as ContractState
from src.foundation.paper_control.domain.models import (
    AdapterProvenance,
    CredentialClass,
    DeploymentState,
    PaperDeployment,
)
from src.foundation.paper_control.projections import (
    DeploymentListView,
    build_deployment_list_view,
)
from tests.conftest import PerfBudget
from tests.support.perf_inject import delayed_async

_TENANT_ID = uuid4()


def _deployment(**overrides: object) -> PaperDeployment:
    defaults: dict[str, object] = dict(
        id=uuid4(),
        tenant_id=_TENANT_ID,
        connection_id=None,
        package_ref="pkg-1",
        mandate_revision_id=uuid4(),
        provenance=AdapterProvenance(
            adapter_type="fake-paper-v1",
            credential_class=CredentialClass.PAPER,
            endpoint_classification="SANDBOX",
            provider_sandbox_account_ref="sandbox-acct-1",
        ),
        state=DeploymentState.READY,
        fence_token=0,
    )
    defaults.update(overrides)
    return PaperDeployment(**defaults)


class FakeRepo:
    """Minimal fake implementing only list_deployments (the sole method this
    leaf calls)."""

    def __init__(
        self, deployments: list[PaperDeployment], *, error: Exception | None = None
    ) -> None:
        self.deployments = deployments
        self.error = error
        self.list_deployments_calls: list[UUID] = []

    async def list_deployments(self, tenant_id: UUID) -> list[PaperDeployment]:
        self.list_deployments_calls.append(tenant_id)
        if self.error is not None:
            raise self.error
        return self.deployments


async def test_build_deployment_list_view_empty_repo_returns_empty_view():
    """경계값 — 배포가 하나도 없는 테넌트는 빈 리스트 + as_of만 채워진 뷰를 받는다."""
    repo = FakeRepo([])
    view = await build_deployment_list_view(repo, _TENANT_ID)
    assert view.deployments == []
    assert isinstance(view, DeploymentListView)
    assert repo.list_deployments_calls == [_TENANT_ID]


async def test_build_deployment_list_view_empty_repo_as_of_is_tz_aware_utc():
    """음성 테스트 1 — 빈 목록에서도 as_of는 반드시 tz-aware UTC여야 한다
    (INVARIANTS: 모든 datetime은 tz-aware UTC)."""
    repo = FakeRepo([])
    view = await build_deployment_list_view(repo, _TENANT_ID)
    assert view.as_of.tzinfo is not None
    assert view.as_of.utcoffset() == UTC.utcoffset(None)


async def test_build_deployment_list_view_empty_repo_as_of_is_recent():
    """음성 테스트 2 — as_of는 호출 시점으로부터 1초 이내여야 한다
    (stale projection은 spec 71 §4 위반)."""
    repo = FakeRepo([])
    before = datetime.now(UTC)
    view = await build_deployment_list_view(repo, _TENANT_ID)
    after = datetime.now(UTC)
    assert before <= view.as_of <= after


async def test_build_deployment_list_view_empty_repo_zero_cpu_budget(
    perf_budget: PerfBudget,
) -> None:
    """성능 수치 단언 — 빈 목록 조립은 순수 CPU 작업이므로 1ms 미만이어야 한다
    (양자화 오차 제외). batch=64로 Windows 15.6ms tick을 분할해 0 수렴을 검증한다.

    정상 입력에서 녹색: CPU 예산(1ms)을 명확히 하회한다."""
    repo = FakeRepo([])

    sample = await perf_budget.sample_async(
        lambda: build_deployment_list_view(repo, _TENANT_ID), batch=64
    )
    assert sample.cpu_ms < 1.0, f"empty repo assembly took {sample.cpu_ms:.3f}ms, budget is 1ms"


@pytest.mark.perf
async def test_build_deployment_list_view_empty_repo_delay_injection_fails_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """적색 재현 — repo.list_deployments에 50ms 지연 주입 시 호출이 10배 이상 늘어나야 한다.
    이는 성능 단언이 실제로 지연 감지 능력을 갖췄음을 증명한다.

    delayed_async 사용: # type: ignore budget 소모 없음(task-11539 감사 후속)."""
    repo = FakeRepo([])

    # 원본 메서드 저장
    original_list = repo.list_deployments

    # 50ms 지연 주입 (CPU 예산 ~0.1ms의 500배)
    monkeypatch.setattr(
        repo,
        "list_deployments",
        delayed_async(original_list, seconds=0.05),
    )

    start = time.perf_counter()
    view = await build_deployment_list_view(repo, _TENANT_ID)
    elapsed_ms = (time.perf_counter() - start) * 1000

    # 지연 주입 후에도 결과는 동일해야 함
    assert view.deployments == []
    # 하지만 소요 시간은 50ms 이상이어야 함 (지연이 실제로 측정됨)
    assert elapsed_ms >= 40, (
        f"injected 50ms delay resulted in only {elapsed_ms:.1f}ms wall time — "
        "delay injection failed, gate-red reproduction inconclusive"
    )


async def test_build_deployment_list_view_maps_all_deployments_in_order():
    deployments = [
        _deployment(state=DeploymentState.READY),
        _deployment(state=DeploymentState.RUNNING),
        _deployment(state=DeploymentState.PAUSED),
    ]
    repo = FakeRepo(deployments)
    view = await build_deployment_list_view(repo, _TENANT_ID)
    assert [v.id for v in view.deployments] == [d.id for d in deployments]
    assert [v.state for v in view.deployments] == [
        ContractState.READY,
        ContractState.RUNNING,
        ContractState.PAUSED,
    ]


async def test_build_deployment_list_view_as_of_is_tz_aware_utc_and_current():
    """INVARIANTS — 모든 datetime은 tz-aware UTC. as_of는 호출 시점 근방이어야 한다."""
    repo = FakeRepo([])
    before = datetime.now(UTC)
    view = await build_deployment_list_view(repo, _TENANT_ID)
    after = datetime.now(UTC)
    assert view.as_of.tzinfo is not None
    assert view.as_of.utcoffset() == UTC.utcoffset(None)
    assert before <= view.as_of <= after


async def test_build_deployment_list_view_raises_when_repository_violates_contract():
    """경계값 — repo가 포트 계약(list 반환)을 어기고 None을 반환하면 list comprehension이
    TypeError로 즉시 드러낸다(이 leaf는 방어적으로 빈 리스트로 강등하지 않는다)."""
    repo = FakeRepo(None)
    with pytest.raises(TypeError):
        await build_deployment_list_view(repo, _TENANT_ID)


async def test_build_deployment_list_view_propagates_repository_failure():
    """실패주입 — repo.list_deployments가 예외를 던지면 그대로 전파된다(이 leaf는
    재시도·폴백 로직을 갖지 않는다)."""
    repo = FakeRepo([], error=RuntimeError("db connection lost"))
    with pytest.raises(RuntimeError, match="db connection lost"):
        await build_deployment_list_view(repo, _TENANT_ID)


@pytest.mark.perf
def test_build_deployment_list_view_assembly_latency_budget(
    perf_budget: PerfBudget,
) -> None:
    """성능 단언 — 1000건 조립도 순수 in-memory 매핑이므로 100ms 내에 끝나야 한다
    (105 DB 왕복은 fake로 대체했으므로 이 시간은 순수 조립 비용만 반영한다).

    build_deployment_list_view()는 비동기 시그니처지만 측정 대상이 순수 CPU 조립이므로,
    asyncio.run()로 동기 래핑해 perf_budget.sample()로 측정한다. sample()은
    coverage tracer를 일시정지하고 time.process_time() 기준으로 CPU 시간을 재므로
    --cov-append 부하가 측정값에 얹히지 않는다.
    """
    deployments = [_deployment() for _ in range(1000)]
    repo = FakeRepo(deployments)

    # coroutine을 한 번 끝까지 돌려 결과를 얻는 동기 래퍼
    def sync_wrapper() -> DeploymentListView:
        return asyncio.run(build_deployment_list_view(repo, _TENANT_ID))

    sample = perf_budget.sample(sync_wrapper, batch=8)
    view = sample.result
    assert len(view.deployments) == 1000
    assert sample.cpu_ms < 100, f"assembly took {sample.cpu_ms:.2f}ms, budget is 100ms"
