"""AI-15 -- `tools_read.py`/`tools_research.py` thin-proxy proof.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §9 AI-15 DoD (I-08),
§2.3 AI-14 (this leaf's only delegate). Every "정상 경로" test compares the
MCP tool's HTTP response against calling the AI-14 delegate directly with
the same input -- proof of delegation, not reimplementation (the same
posture `tests/foundation/unit/ai/factory/test_research_tools.py`'s own
docstring states for AI-14 itself, one layer up). ADR-2026-09-09-C D2:
negative >=3 (this file adds 5 tool-specific negatives below, not counted
jointly with test_server_auth.py).

`tenant_id` for every experiment tool call comes from the issued
`AgentToken`, not a request field -- the cross-tenant test below proves an
agent token from tenant A cannot read tenant B's experiment by any means
this HTTP surface exposes (no `tenant_id` field even exists on the wire
schema, see `src/api/mcp/tools_research.py`'s request models).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.mcp.server import AGENT_TOKEN_HEADER, create_mcp_app
from src.core.indicators.engine.vectorized import compute as compute_indicator_direct
from src.foundation.ai.factory.application import research_tools
from src.foundation.ai.gateway.adapters.postgres_token_repository import (
    PostgresAgentTokenRepository,
)
from src.foundation.ai.gateway.domain.token_rules import Scope
from src.foundation.experiments.adapters.postgres_repository import PostgresExperimentRepository
from src.foundation.experiments.contracts.v1 import Experiment, ExperimentKind
from tests.api.mcp.conftest import issue
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _experiment(tenant_id, *, reproducibility_key: str, inputs_hash: str) -> Experiment:
    return Experiment(
        experiment_id=uuid4(),
        tenant_id=tenant_id,
        reproducibility_key=reproducibility_key,
        kind=ExperimentKind.BACKTEST,
        inputs_hash=inputs_hash,
        metrics={"sharpe_ratio": 1.2},
        created_by=uuid4(),
        created_at=_T0,
    )


@pytest.fixture
async def client(pool):  # noqa: ANN001 -- type is asyncpg.Pool
    app = create_mcp_app(pool)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://mcp.test") as c:
        yield c


@pytest.fixture
def experiment_repo(pool) -> PostgresExperimentRepository:  # noqa: ANN001
    return PostgresExperimentRepository(pool)


# --- read: compute_indicator는 IND-1과 동일 산출물을 낸다(위임 증명) ---


async def test_compute_indicator_tool_matches_direct_call(
    client, token_repo: PostgresAgentTokenRepository
):
    issued = await issue(token_repo, tenant_id=uuid4(), scopes=frozenset({Scope.READ}))
    columns = {"close": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]}
    body = {"name": "SMA", "columns": columns, "params": {"timeperiod": 3}}

    response = await client.post(
        "/mcp/tools/compute_indicator", json=body, headers={AGENT_TOKEN_HEADER: issued.secret}
    )
    assert response.status_code == 200

    direct = compute_indicator_direct("SMA", columns, {"timeperiod": 3})
    expected = [None if x != x else x for x in direct["value"].tolist()]  # NaN -> None (JSON null)
    got = [None if x is None else x for x in response.json()["values"]["value"]]
    assert got == pytest.approx(expected, nan_ok=True)


# --- read negative: 알 수 없는 지표는 400(도구가 서버로 위장하지 않음) ---


async def test_compute_indicator_tool_rejects_unknown_indicator(
    client, token_repo: PostgresAgentTokenRepository
):
    issued = await issue(token_repo, tenant_id=uuid4(), scopes=frozenset({Scope.READ}))
    response = await client.post(
        "/mcp/tools/compute_indicator",
        json={"name": "NOT_A_REAL_INDICATOR", "columns": {"close": [1.0, 2.0]}},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 400


# --- research: 실험 조회는 AI-11 결과를 그대로 반환(위임 증명) ---


async def test_get_experiment_context_tool_returns_stored_experiment(
    client, token_repo: PostgresAgentTokenRepository, experiment_repo: PostgresExperimentRepository
):
    tenant_id = uuid4()
    experiment = _experiment(tenant_id, reproducibility_key="a" * 64, inputs_hash="b" * 64)
    await experiment_repo.append(experiment)
    issued = await issue(token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.RESEARCH}))

    response = await client.post(
        "/mcp/tools/get_experiment_context",
        json={"experiment_id": str(experiment.experiment_id)},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 200
    assert response.json()["reproducibility_key"] == "a" * 64
    assert response.json()["experiment_id"] == str(experiment.experiment_id)


# --- research negative: 존재하지 않는 experiment_id는 404 ---


async def test_get_experiment_context_tool_unknown_id_is_404(
    client, token_repo: PostgresAgentTokenRepository
):
    issued = await issue(token_repo, tenant_id=uuid4(), scopes=frozenset({Scope.RESEARCH}))
    response = await client.post(
        "/mcp/tools/get_experiment_context",
        json={"experiment_id": str(uuid4())},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 404


# --- research negative (교차 테넌트): 다른 테넌트 소유 실험은 404로 위장(존재 비공개) ---


async def test_get_experiment_context_tool_cross_tenant_is_404(
    client, token_repo: PostgresAgentTokenRepository, experiment_repo: PostgresExperimentRepository
):
    owner_tenant = uuid4()
    experiment = _experiment(owner_tenant, reproducibility_key="c" * 64, inputs_hash="d" * 64)
    await experiment_repo.append(experiment)

    attacker_tenant = uuid4()
    issued = await issue(token_repo, tenant_id=attacker_tenant, scopes=frozenset({Scope.RESEARCH}))

    response = await client.post(
        "/mcp/tools/get_experiment_context",
        json={"experiment_id": str(experiment.experiment_id)},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 404  # not 200, not 403 -- existence is not leaked either


# --- research: list_experiment_reproductions는 재현 키로 위임 조회한다 ---


async def test_list_experiment_reproductions_tool_thin_proxy(
    client, token_repo: PostgresAgentTokenRepository, experiment_repo: PostgresExperimentRepository
):
    tenant_id = uuid4()
    key = "e" * 64
    first = _experiment(tenant_id, reproducibility_key=key, inputs_hash="f" * 64)
    second = _experiment(tenant_id, reproducibility_key=key, inputs_hash="f" * 64)
    await experiment_repo.append(first)
    await experiment_repo.append(second)
    issued = await issue(token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.RESEARCH}))

    response = await client.post(
        "/mcp/tools/list_experiment_reproductions",
        json={"reproducibility_key": key},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 200
    ids = {row["experiment_id"] for row in response.json()}
    assert ids == {str(first.experiment_id), str(second.experiment_id)}


# --- research negative: 재현 세트가 비어 있으면 404(compare_experiment_reproductions) ---


async def test_compare_experiment_reproductions_tool_none_found_is_404(
    client, token_repo: PostgresAgentTokenRepository
):
    issued = await issue(token_repo, tenant_id=uuid4(), scopes=frozenset({Scope.RESEARCH}))
    response = await client.post(
        "/mcp/tools/compare_experiment_reproductions",
        json={"reproducibility_key": "0" * 64},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 404


# --- research negative: experiment_id가 UUID가 아니면 위임 전에 422(위임 대상까지 안 감) ---


async def test_get_experiment_context_tool_malformed_id_is_422(
    client, token_repo: PostgresAgentTokenRepository
):
    issued = await issue(token_repo, tenant_id=uuid4(), scopes=frozenset({Scope.RESEARCH}))
    response = await client.post(
        "/mcp/tools/get_experiment_context",
        json={"experiment_id": "not-a-uuid"},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 422


# --- 실패 주입: AI-14 위임 대상이 예외를 내면 200/404로 위장하지 않고 500으로 전파 ---


async def test_get_experiment_context_tool_delegate_failure_is_not_swallowed(
    client, token_repo: PostgresAgentTokenRepository, monkeypatch: pytest.MonkeyPatch
):
    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("experiment ledger unreachable")

    monkeypatch.setattr(research_tools, "get_experiment_context", _boom)
    issued = await issue(token_repo, tenant_id=uuid4(), scopes=frozenset({Scope.RESEARCH}))

    response = await client.post(
        "/mcp/tools/get_experiment_context",
        json={"experiment_id": str(uuid4())},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 500  # not 404/200 -- delegate failure is not disguised


# --- compute_indicator negative: required field 누락은 422(위임 전 Pydantic 검증) ---


async def test_compute_indicator_tool_missing_columns_is_422(
    client, token_repo: PostgresAgentTokenRepository
):
    issued = await issue(token_repo, tenant_id=uuid4(), scopes=frozenset({Scope.READ}))
    response = await client.post(
        "/mcp/tools/compute_indicator",
        json={"name": "SMA"},  # columns missing
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 422


async def test_compute_indicator_tool_missing_name_is_422(
    client, token_repo: PostgresAgentTokenRepository
):
    issued = await issue(token_repo, tenant_id=uuid4(), scopes=frozenset({Scope.READ}))
    response = await client.post(
        "/mcp/tools/compute_indicator",
        json={"columns": {"close": [1.0, 2.0]}},  # name missing
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 422


async def test_compute_indicator_tool_empty_columns_is_400(
    client, token_repo: PostgresAgentTokenRepository
):
    """Empty columns dict triggers IndicatorError (unknown indicator / no data) → 400."""
    issued = await issue(token_repo, tenant_id=uuid4(), scopes=frozenset({Scope.READ}))
    response = await client.post(
        "/mcp/tools/compute_indicator",
        json={"name": "SMA", "columns": {}},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 400


# --- 실패 주입: compute_indicator 위임 대상이 예외를 내면 500으로 전파 ---


async def test_compute_indicator_tool_delegate_failure_is_not_swallowed(
    client, token_repo: PostgresAgentTokenRepository, monkeypatch: pytest.MonkeyPatch
):
    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("indicator engine unreachable")

    monkeypatch.setattr(research_tools, "compute_indicator", _boom)
    issued = await issue(token_repo, tenant_id=uuid4(), scopes=frozenset({Scope.READ}))

    response = await client.post(
        "/mcp/tools/compute_indicator",
        json={"name": "SMA", "columns": {"close": [1.0, 2.0]}, "params": {"timeperiod": 3}},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 500  # not 400/200 -- delegate failure is not disguised


# --- 성능 단언: compute_indicator 호출이 CPU 예산 내야 한다 ---


@pytest.mark.perf
async def test_compute_indicator_tool_performance_under_normal_load(
    client, token_repo: PostgresAgentTokenRepository, perf_budget: PerfBudget
):
    """성능 단언 (D2, task-24356): 정상적인 compute_indicator 호출이 CPU 예산 내야 한다.

    실측 기준: 이 워크호스트에서 compute_indicator(8바, SMA timeperiod=3)는
    ~0.3ms CPU. 30배 여유인 10ms를 예산으로 설정한다.
    """
    reset_virtual_totals()
    issued = await issue(token_repo, tenant_id=uuid4(), scopes=frozenset({Scope.READ}))
    columns = {"close": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]}

    async def _call() -> None:
        await client.post(
            "/mcp/tools/compute_indicator",
            json={"name": "SMA", "columns": columns, "params": {"timeperiod": 3}},
            headers={AGENT_TOKEN_HEADER: issued.secret},
        )

    sample = await perf_budget.best_of_async(_call, n=5, warmup=1)
    # 실측 ~0.3ms × 30 = 10ms 예산
    assert sample.cpu_ms < 10, (
        f"compute_indicator가 예산을 초과: {perf_budget.describe(sample, budget_ms=10)}"
    )


# --- 게이트 적색 재현: virtual_delayed_sync로 가상 지연 주입 → 단언이 적색 전환 ---


@pytest.mark.perf
async def test_compute_indicator_tool_perf_gate_red_virtual_delay(
    client,
    token_repo: PostgresAgentTokenRepository,
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
):
    """게이트 적색 재현 (D2, task-24356): virtual_delayed_sync로 가상 지연
    (예산 10배)을 주입하고, 같은 CPU 단언이 초과함을 확인한다.

    실제 sleep/busy-wait 금지 — virtual_delayed_sync가 virtual ledger에
    나노초만 더하므로 측정치에 지연을 더할 뿐 실제 테스트 시간이 늘어나지
    않는다. green 테스트와 동일한 `cpu_ms < 10` 단언이 지연 주입 시
    `cpu_ms >= 10`로 적색을 재현한다.
    """
    reset_virtual_totals()
    budget_ms = 10.0
    # 10배 가상 지연 (budget_ms * 10 / 1000 → 초 단위)
    injected_delay_s = budget_ms * 10 / 1000.0

    issued = await issue(token_repo, tenant_id=uuid4(), scopes=frozenset({Scope.READ}))
    columns = {"close": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]}

    # compute_indicator에 가상 지연 주입
    orig_compute = research_tools.compute_indicator
    research_tools.compute_indicator = virtual_delayed_sync(orig_compute, injected_delay_s)

    try:

        async def _call() -> None:
            await client.post(
                "/mcp/tools/compute_indicator",
                json={"name": "SMA", "columns": columns, "params": {"timeperiod": 3}},
                headers={AGENT_TOKEN_HEADER: issued.secret},
            )

        red = await perf_budget.best_of_async(_call, n=3, warmup=0)
        # green 테스트와 동일한 단언이 적색으로 전환됨을 확인
        assert red.cpu_ms >= budget_ms, (
            f"가상 지연 주입에도 CPU 예산 미만: {perf_budget.describe(red, budget_ms=budget_ms)}"
        )
    finally:
        research_tools.compute_indicator = orig_compute
