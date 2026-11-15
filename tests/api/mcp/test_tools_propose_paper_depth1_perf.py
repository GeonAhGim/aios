"""AI-16 -- performance / gate-red evidence for `tools_propose.py`/`tools_paper.py`.

DEEPEN-DEPTH task-25215: gate-red reproduction + performance assertions.
Imports/fixtures mirror `test_tools_propose_paper.py` and `conftest.py`.
Negative/failure-injection tests live in `test_tools_propose_paper_depth1.py`.
"""

from __future__ import annotations

import asyncpg
import pytest

from src.api.mcp.server import AGENT_TOKEN_HEADER
from src.foundation.ai.gateway.adapters.postgres_token_repository import (
    PostgresAgentTokenRepository,
)
from src.foundation.ai.gateway.domain.token_rules import Scope
from tests.api.mcp.conftest import issue
from tests.api.mcp.test_tools_propose_paper import (
    _accepted_experiment,
    _promotion_params,
    _proposal_ready_tenant,
    _submit_proposal,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import (
    reset_virtual_totals,
    virtual_delayed_async,
)

pytestmark = pytest.mark.usefixtures(
    "pool",
    "client",
    "mandate_repo",
    "trust_repo",
    "coverage_repo",
    "instrument_repo",
    "experiment_repo",
)


# --- 게이트 적색 재현: 가상 지연 10x 주입으로 성능 예산 위반 확인 ---


@pytest.mark.perf
async def test_confirm_promotion_performance_budget_catches_10x_delay_injection(
    pool,
    client,
    mandate_repo,
    trust_repo,
    coverage_repo,
    instrument_repo,
    experiment_repo,
    token_repo: PostgresAgentTokenRepository,
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
):
    """게이트 적색 재현(D2): 성능 단언이 실제로 지연을 포착하는지 역으로 증명.

    `asyncpg.Connection.fetchrow`에 10초 가상 지연 주입 ->
    `PerfBudget.sample_async` 측정치가 budget 5s 초과로 적색.
    virtual_delayed_async는 실제 sleep 없이 측정치에만 합산.

    실측 근거: 가상 지연 없이 propose->preview->confirm ~ 200ms CPU.
    10x 지연(10s) 주입 시 ~ 10.2s -> budget 5s 초과로 적색.
    예산 5000ms는 실측의 ~25배.
    """
    reset_virtual_totals()

    tenant_id, instrument_id = await _proposal_ready_tenant(
        pool, mandate_repo, trust_repo, coverage_repo, instrument_repo
    )
    issued = await issue(
        token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.PROPOSE, Scope.PAPER})
    )
    proposal = await _submit_proposal(client, issued.secret, instrument_id)
    experiment = _accepted_experiment(tenant_id)
    await experiment_repo.append(experiment)

    params = _promotion_params(
        proposal_id=proposal["proposal_id"],
        experiment_id=experiment.experiment_id,
        idempotency_key=f"promo-gate-red-{tenant_id}",
    )

    # 녹색 제어군
    preview = await client.post(
        "/mcp/tools/preview_promotion",
        json=params,
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert preview.status_code == 200, preview.text
    ticket_id = preview.json()["ticket_id"]

    budget_ms = 5000.0
    sample = await perf_budget.sample_async(
        lambda: client.post(
            "/mcp/tools/confirm_promotion",
            json={**params, "ticket_id": ticket_id},
            headers={AGENT_TOKEN_HEADER: issued.secret},
        )
    )
    confirm = sample.result
    assert confirm.status_code == 200, confirm.text
    assert sample.wall_ms < budget_ms, "normal round-trip exceeds budget: " + perf_budget.describe(
        sample, budget_ms=budget_ms
    )

    # 적색 주입 -- fetchrow에 10초 가상 지연
    monkeypatch.setattr(
        asyncpg.Connection,
        "fetchrow",
        virtual_delayed_async(asyncpg.Connection.fetchrow, 10.0),
    )

    # 새 ticket 발급 (지연 주입 이후)
    preview2 = await client.post(
        "/mcp/tools/preview_promotion",
        json=params,
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert preview2.status_code == 200, preview2.text
    ticket_id2 = preview2.json()["ticket_id"]

    # 게이트 적색: 측정치가 budget을 초과해야 함
    sample_red = await perf_budget.sample_async(
        lambda: client.post(
            "/mcp/tools/confirm_promotion",
            json={**params, "ticket_id": ticket_id2},
            headers={AGENT_TOKEN_HEADER: issued.secret},
        )
    )
    assert sample_red.wall_ms >= budget_ms, (
        f"gate-red failed: expected wall >= {budget_ms}ms but got "
        f"{sample_red.wall_ms:.1f}ms -- perf_budget must catch budget violation"
    )


# --- 성능 단언: best_of(5)로 노이즈 제거한 최소 CPU 시간 단언 ---


@pytest.mark.perf
async def test_confirm_promotion_performance_budget_best_of(
    pool,
    client,
    mandate_repo,
    trust_repo,
    coverage_repo,
    instrument_repo,
    experiment_repo,
    token_repo: PostgresAgentTokenRepository,
    perf_budget: PerfBudget,
):
    """성능 단언(D2): propose->preview->confirm 전체 라운드트립이
    best_of(5) 최소 CPU 시간으로 5초 이내.

    실측 근거: 로컬 ~ 200ms CPU. 예산 5000ms는 실측의 ~25배.
    """
    reset_virtual_totals()

    tenant_id, instrument_id = await _proposal_ready_tenant(
        pool, mandate_repo, trust_repo, coverage_repo, instrument_repo
    )
    issued = await issue(
        token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.PROPOSE, Scope.PAPER})
    )

    async def _round_trip() -> object:
        proposal = await _submit_proposal(client, issued.secret, instrument_id)
        experiment = _accepted_experiment(tenant_id)
        await experiment_repo.append(experiment)
        params = _promotion_params(
            proposal_id=proposal["proposal_id"],
            experiment_id=experiment.experiment_id,
            idempotency_key=f"promo-bestof-{tenant_id}",
        )
        preview = await client.post(
            "/mcp/tools/preview_promotion",
            json=params,
            headers={AGENT_TOKEN_HEADER: issued.secret},
        )
        assert preview.status_code == 200, preview.text
        ticket_id = preview.json()["ticket_id"]
        return await client.post(
            "/mcp/tools/confirm_promotion",
            json={**params, "ticket_id": ticket_id},
            headers={AGENT_TOKEN_HEADER: issued.secret},
        )

    budget_ms = 5000.0
    sample = await perf_budget.sample_async(_round_trip)
    confirm = sample.result
    assert confirm.status_code == 200, confirm.text
    assert sample.wall_ms < budget_ms, (
        "propose->preview->confirm exceeds 5s budget: "
        + perf_budget.describe(sample, budget_ms=budget_ms)
    )
