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

import time
from typing import Any

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

    start = time.perf_counter()

    proposal = await _submit_proposal(client, issued.secret, instrument_id)
    experiment = _accepted_experiment(tenant_id)
    await experiment_repo.append(experiment)
    params = _promotion_params(
        proposal_id=proposal["proposal_id"],
        experiment_id=experiment.experiment_id,
        idempotency_key=f"promo-perf-{tenant_id}",
    )
    preview = await client.post(
        "/mcp/tools/preview_promotion", json=params, headers={AGENT_TOKEN_HEADER: issued.secret}
    )
    assert preview.status_code == 200, preview.text
    ticket_id = preview.json()["ticket_id"]

    confirm = await client.post(
        "/mcp/tools/confirm_promotion",
        json={**params, "ticket_id": ticket_id},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    elapsed_sec = time.perf_counter() - start

    assert confirm.status_code == 200, confirm.text
    assert elapsed_sec < 5.0, (
        f"propose->preview->confirm took {elapsed_sec:.2f}s, exceeds 5s budget"
    )
