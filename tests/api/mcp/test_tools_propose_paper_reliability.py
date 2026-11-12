"""AI-16 -- `tools_propose.py`/`tools_paper.py` reliability evidence.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §9 AI-16 DoD.
DEEPEN split (task-10072) from `test_tools_propose_paper.py`: the round-trip
and negative-status tests already meet D2's negative >= 3 floor in that file;
this sibling adds the remaining D2 floor items so neither file crosses the
500-line ratchet warn threshold (CLAUDE.md §6 mistake #12).

Failure injection: `confirm_promotion`/`preview_promotion` have no bare
`except Exception` clause in `src/api/mcp/tools_paper.py` -- an unexpected
repository failure must surface as a generic 500 through Starlette's own
exception middleware, not leak the underlying exception text to the MCP
caller (INVARIANTS.md I-11 fail-closed posture).
Performance: a full propose -> preview -> confirm round trip against the
test DB is asserted under a fixed wall-clock budget (ADR-2026-09-09-C
Decision 1's "numeric performance assertion" D2 floor).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

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
from tests.support.perf_inject import (
    reset_virtual_totals,
    virtual_delayed_sync,
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


# --- 실패주입 #1: proposal_repo.get_for_tenant 예외 -> 500, 내부 오류 미노출 ---


async def test_confirm_promotion_failure_injection_proposal_lookup_raises(
    pool: Any,
    client: Any,
    mandate_repo: Any,
    trust_repo: Any,
    coverage_repo: Any,
    instrument_repo: Any,
    experiment_repo: Any,
    token_repo: PostgresAgentTokenRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`PostgresProposalRepository.get_for_tenant` raising must surface as a
    generic 500, not leak the underlying exception text to the caller."""
    from src.foundation.ai.factory.adapters.postgres_proposal_repository import (
        PostgresProposalRepository,
    )

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
        idempotency_key=f"promo-inject-{tenant_id}",
    )
    preview = await client.post(
        "/mcp/tools/preview_promotion", json=params, headers={AGENT_TOKEN_HEADER: issued.secret}
    )
    assert preview.status_code == 200, preview.text
    ticket_id = preview.json()["ticket_id"]

    async def _boom(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise RuntimeError("synthetic DB connection lost")

    monkeypatch.setattr(PostgresProposalRepository, "get_for_tenant", _boom)

    response = await client.post(
        "/mcp/tools/confirm_promotion",
        json={**params, "ticket_id": ticket_id},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )

    assert response.status_code == 500, response.text
    assert "synthetic db connection lost" not in response.text.lower()


# --- 실패주입 #2: confirm_repo.issue 예외 -> preview_promotion도 500, 미노출 ---


async def test_preview_promotion_failure_injection_ticket_issue_raises(
    pool: Any,
    client: Any,
    mandate_repo: Any,
    trust_repo: Any,
    coverage_repo: Any,
    instrument_repo: Any,
    experiment_repo: Any,
    token_repo: PostgresAgentTokenRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`PostgresConfirmTicketRepository.issue` raising must surface as a
    generic 500 from `preview_promotion` as well, not just `confirm_promotion`."""
    from src.foundation.ai.gateway.adapters.postgres_confirm_ticket_repository import (
        PostgresConfirmTicketRepository,
    )

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
        idempotency_key=f"promo-inject2-{tenant_id}",
    )

    async def _boom(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise RuntimeError("synthetic ticket table deadlock")

    monkeypatch.setattr(PostgresConfirmTicketRepository, "issue", _boom)

    response = await client.post(
        "/mcp/tools/preview_promotion", json=params, headers={AGENT_TOKEN_HEADER: issued.secret}
    )

    assert response.status_code == 500, response.text
    assert "synthetic ticket table deadlock" not in response.text.lower()


# --- 음성(negative) #2: 유효하지 않은 UUID -> 422 validation error ---


async def test_preview_promotion_invalid_uuid_proposal_id_is_422(
    pool: Any,
    client: Any,
    mandate_repo: Any,
    trust_repo: Any,
    coverage_repo: Any,
    instrument_repo: Any,
    token_repo: PostgresAgentTokenRepository,
) -> None:
    """`preview_promotion` with a non-UUID `proposal_id` must return 422
    (Pydantic/Starlette auto-validation on the `PromotionParams` schema)."""
    tenant_id, _instrument_id = await _proposal_ready_tenant(
        pool, mandate_repo, trust_repo, coverage_repo, instrument_repo
    )
    issued = await issue(
        token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.PROPOSE, Scope.PAPER})
    )
    params = _promotion_params(
        proposal_id="not-a-uuid",
        experiment_id=uuid4(),
        idempotency_key="neg-invalid-uuid",
    )
    response = await client.post(
        "/mcp/tools/preview_promotion",
        json=params,
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 422, response.text


# --- 음성(negative) #3: 누락된 필수 필드 adapter_type -> 422 ---


async def test_preview_promotion_missing_required_field_adapter_type_is_422(
    pool: Any,
    client: Any,
    mandate_repo: Any,
    trust_repo: Any,
    coverage_repo: Any,
    instrument_repo: Any,
    token_repo: PostgresAgentTokenRepository,
) -> None:
    """`preview_promotion` missing `adapter_type` (required str) must return 422."""
    tenant_id, _instrument_id = await _proposal_ready_tenant(
        pool, mandate_repo, trust_repo, coverage_repo, instrument_repo
    )
    issued = await issue(
        token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.PROPOSE, Scope.PAPER})
    )
    params = _promotion_params(
        proposal_id=str(uuid4()),
        experiment_id=uuid4(),
        idempotency_key="neg-missing-field",
    )
    del params["adapter_type"]
    response = await client.post(
        "/mcp/tools/preview_promotion",
        json=params,
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 422, response.text


# --- 음성(negative) #4: confirm_promotion에 존재하지 않는 proposal_id 전달 -> 404 ---


async def test_confirm_promotion_nonexistent_proposal_id_is_404(
    pool: Any,
    client: Any,
    mandate_repo: Any,
    trust_repo: Any,
    coverage_repo: Any,
    instrument_repo: Any,
    experiment_repo: Any,
    token_repo: PostgresAgentTokenRepository,
) -> None:
    """`confirm_promotion` with a `proposal_id` that does not exist in the
    database must return 404 (not 500 or 200)."""
    tenant_id, instrument_id = await _proposal_ready_tenant(
        pool, mandate_repo, trust_repo, coverage_repo, instrument_repo
    )
    issued = await issue(
        token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.PROPOSE, Scope.PAPER})
    )
    experiment = _accepted_experiment(tenant_id)
    await experiment_repo.append(experiment)
    fake_proposal_id = str(uuid4())
    params = _promotion_params(
        proposal_id=fake_proposal_id,
        experiment_id=experiment.experiment_id,
        idempotency_key="neg-no-proposal",
    )
    preview = await client.post(
        "/mcp/tools/preview_promotion",
        json=params,
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert preview.status_code == 200, preview.text
    ticket_id = preview.json()["ticket_id"]
    response = await client.post(
        "/mcp/tools/confirm_promotion",
        json={**params, "ticket_id": ticket_id},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 404, response.text


# --- 게이트 적색 재현: 가상 지연 주입으로 perf 단언이 초과함을 확인 ---


@pytest.mark.perf
async def test_confirm_promotion_round_trip_perf_gate_red_virtual_delay(
    pool: Any,
    client: Any,
    mandate_repo: Any,
    trust_repo: Any,
    coverage_repo: Any,
    instrument_repo: Any,
    experiment_repo: Any,
    token_repo: PostgresAgentTokenRepository,
    perf_budget: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현 (D2): 가상 지연(budget * 10)을 주입하고, 같은 CPU
    단언이 초과함을 확인한다.

    virtual_delayed_sync가 virtual ledger에 나노초만 더하므로 실제 테스트
    시간이 늘어나지 않는다. green 테스트와 동일한 `cpu_ms < 5000` 단언이
    지연 주입 시 `cpu_ms >= 5000`으로 적색을 재현한다.
    """
    reset_virtual_totals()
    budget_ms = 5000.0
    # 10배 가상 지연 (budget_ms * 10 / 1000 → 초 단위)
    injected_delay_s = budget_ms * 10 / 1000.0

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
        idempotency_key=f"promo-gatered-{tenant_id}",
    )

    # Inject virtual delay into promote_to_paper — the slowest operation in
    # the confirm path. This is the same pattern used in
    # test_tools_thin_proxy.py::test_compute_indicator_tool_perf_gate_red_virtual_delay.
    from src.foundation.ai.factory.application.promote_to_paper import promote_to_paper

    orig_promote = promote_to_paper
    promoted = virtual_delayed_sync(orig_promote, injected_delay_s)

    try:
        monkeypatch.setattr(
            "src.api.mcp.tools_paper.promote_to_paper",
            promoted,
        )

        async def _round_trip() -> Any:
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

        red = await perf_budget.best_of_async(_round_trip, n=3, warmup=0)
        # green 테스트와 동일한 단언이 적색으로 전환됨을 확인
        assert red.cpu_ms >= budget_ms, (
            f"가상 지연 주입에도 CPU 예산 미만: {perf_budget.describe(red, budget_ms=budget_ms)}"
        )
    finally:
        monkeypatch.setattr(
            "src.api.mcp.tools_paper.promote_to_paper",
            orig_promote,
        )


# --- 성능 단언: propose -> preview -> confirm 왕복 지연 예산 ---


@pytest.mark.perf
async def test_confirm_promotion_round_trip_latency_budget(
    pool: Any,
    client: Any,
    mandate_repo: Any,
    trust_repo: Any,
    coverage_repo: Any,
    instrument_repo: Any,
    experiment_repo: Any,
    token_repo: PostgresAgentTokenRepository,
    perf_budget: Any,
) -> None:
    """Full propose -> preview -> confirm round trip against the test DB
    must complete within a fixed wall-clock budget (ADR-2026-09-09-C
    Decision 1's numeric performance assertion D2 floor)."""
    tenant_id, instrument_id = await _proposal_ready_tenant(
        pool, mandate_repo, trust_repo, coverage_repo, instrument_repo
    )
    issued = await issue(
        token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.PROPOSE, Scope.PAPER})
    )

    async def _round_trip() -> Any:
        proposal = await _submit_proposal(client, issued.secret, instrument_id)
        experiment = _accepted_experiment(tenant_id)
        await experiment_repo.append(experiment)
        params = _promotion_params(
            proposal_id=proposal["proposal_id"],
            experiment_id=experiment.experiment_id,
            idempotency_key=f"promo-perf-{tenant_id}",
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
    confirm: Any = sample.result

    assert confirm.status_code == 200, confirm.text
    assert sample.wall_ms < budget_ms, (
        "propose->preview->confirm exceeds 5s budget: "
        + perf_budget.describe(sample, budget_ms=budget_ms)
    )
