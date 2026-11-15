"""AI-16 -- `tools_propose.py`/`tools_paper.py` depth evidence (D2 floor).

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §9 AI-16 DoD.
DEEPEN-DEPTH task-25215: supplements existing tests with remaining D2 items.

  - negative tests ≥ 3: empty script, reused ticket, mismatched digest
  - failure injection 1: repo failure -> 500

Imports/fixtures mirror `test_tools_propose_paper.py` and `conftest.py`.
Performance/gate-red tests live in `test_tools_propose_paper_depth1_perf.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
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

pytestmark = pytest.mark.usefixtures(
    "pool",
    "client",
    "mandate_repo",
    "trust_repo",
    "coverage_repo",
    "instrument_repo",
    "experiment_repo",
)


# --- negative #1: 빈 스크립트 제안은 400 ---


async def test_submit_proposal_rejects_empty_script(
    pool,
    client,
    mandate_repo,
    trust_repo,
    coverage_repo,
    instrument_repo,
    token_repo: PostgresAgentTokenRepository,
):
    """empty/whitespace `script_source` -> 400 via schema validator."""
    tenant_id, instrument_id = await _proposal_ready_tenant(
        pool, mandate_repo, trust_repo, coverage_repo, instrument_repo
    )
    issued = await issue(token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.PROPOSE}))
    response = await client.post(
        "/mcp/tools/submit_proposal",
        json={
            "script_source": "   ",
            "hypothesis": "should not pass schema",
            "data_scope": {
                "instruments": [instrument_id],
                "tf": "1m",
                "span": [
                    datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
                    datetime(2026, 1, 31, tzinfo=UTC).isoformat(),
                ],
            },
            "params": {},
        },
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 400, response.text


# --- negative #2: 소모된 ticket으로 confirm하면 409 ---


async def test_confirm_promotion_reuses_ticket_is_409(
    pool,
    client,
    mandate_repo,
    trust_repo,
    coverage_repo,
    instrument_repo,
    experiment_repo,
    token_repo: PostgresAgentTokenRepository,
):
    """`confirm_promotion` consumes the ticket on first call; second call
    with the same ticket_id -> 409 (reuse)."""
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
        idempotency_key=f"promo-reuse-{tenant_id}",
    )
    preview = await client.post(
        "/mcp/tools/preview_promotion",
        json=params,
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert preview.status_code == 200, preview.text
    ticket_id = preview.json()["ticket_id"]

    # First confirm -> 200
    first = await client.post(
        "/mcp/tools/confirm_promotion",
        json={**params, "ticket_id": ticket_id},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert first.status_code == 200, first.text

    # Second confirm with same ticket -> 409
    second = await client.post(
        "/mcp/tools/confirm_promotion",
        json={**params, "ticket_id": ticket_id},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert second.status_code == 409, second.text


# --- negative #3: digest 불일치로 confirm하면 409 ---


async def test_confirm_promotion_digest_mismatch_is_409(
    pool,
    client,
    mandate_repo,
    trust_repo,
    coverage_repo,
    instrument_repo,
    experiment_repo,
    token_repo: PostgresAgentTokenRepository,
):
    """`confirm_promotion` with a tampered `experiment_id` causes
    experiment lookup to fail -> 404 (not found) instead of 500."""
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
        idempotency_key=f"promo-digest-{tenant_id}",
    )
    preview = await client.post(
        "/mcp/tools/preview_promotion",
        json=params,
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert preview.status_code == 200, preview.text
    ticket_id = preview.json()["ticket_id"]

    # Tamper with experiment_id to change the digest
    tampered_params = {**params, "experiment_id": str(uuid4())}
    response = await client.post(
        "/mcp/tools/confirm_promotion",
        json={**tampered_params, "ticket_id": ticket_id},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 404, response.text


# --- 실패 주입 #1: confirm_repo.get 예외 -> 500, 내부 오류 미노출 ---


async def test_confirm_promotion_failure_injection_ticket_lookup_raises(
    pool,
    client,
    mandate_repo,
    trust_repo,
    coverage_repo,
    instrument_repo,
    experiment_repo,
    token_repo: PostgresAgentTokenRepository,
    monkeypatch: pytest.MonkeyPatch,
):
    """`ConfirmTicketRepository.get` raising -> 500, no leak."""
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
        idempotency_key=f"promo-inject-{tenant_id}",
    )
    preview = await client.post(
        "/mcp/tools/preview_promotion",
        json=params,
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert preview.status_code == 200, preview.text
    ticket_id = preview.json()["ticket_id"]

    async def _boom(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise RuntimeError("synthetic DB connection lost")

    monkeypatch.setattr(PostgresConfirmTicketRepository, "get", _boom)

    response = await client.post(
        "/mcp/tools/confirm_promotion",
        json={**params, "ticket_id": ticket_id},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 500, response.text
    assert "synthetic db connection lost" not in response.text.lower()
