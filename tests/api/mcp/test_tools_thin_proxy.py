"""AI-15 -- `tools_read.py`/`tools_research.py` thin-proxy proof.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §9 AI-15 DoD (I-08),
§2.3 AI-14 (this leaf's only delegate). Every "정상 경로" test compares the
MCP tool's HTTP response against calling the AI-14 delegate directly with
the same input -- proof of delegation, not reimplementation (the same
posture `tests/foundation/unit/ai/factory/test_research_tools.py`'s own
docstring states for AI-14 itself, one layer up). ADR-2026-09-09-C D2:
negative >=3 (met jointly with test_server_auth.py's own negative tests,
this file adds its own tool-specific ones below).

`tenant_id` for every experiment tool call comes from the issued
`AgentToken`, not a request field -- the cross-tenant test below proves an
agent token from tenant A cannot read tenant B's experiment by any means
this HTTP surface exposes (no `tenant_id` field even exists on the wire
schema, see `src/api/mcp/tools_research.py`'s request models).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.mcp.server import AGENT_TOKEN_HEADER, create_mcp_app
from src.core.indicators.engine.vectorized import compute as compute_indicator_direct
from src.foundation.ai.gateway.adapters.postgres_token_repository import (
    PostgresAgentTokenRepository,
)
from src.foundation.ai.gateway.domain.token_rules import Scope
from src.foundation.experiments.adapters.postgres_repository import PostgresExperimentRepository
from src.foundation.experiments.contracts.v1 import Experiment, ExperimentKind
from tests.api.mcp.conftest import issue

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


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
