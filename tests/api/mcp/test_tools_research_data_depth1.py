"""RD-16 depth-1 supplement for `research_data_search` (D2 floor).

Adds negative tests x7 (3 domain-level + 4 auth/validation), failure injection x1,
gate-red reproduction x1 and a best-of perf assertion x1 on top of
`test_tools_research_data.py` (unchanged).
Fixtures/helpers are reused from that module.

Perf budget (new): one `research_data_search` call over 20 seeded items must
stay under 3000 ms wall (best of 5, warmup 1). Local measurement 2026-11-14:
best-of wall ~3.0 ms, so the budget has ~1000x headroom (>=30x required).
The gate-red test injects a virtual delay of 10x the budget and asserts
the same budget assertion goes red.
"""

# ratchet-allow: get_item not called by research_data_search (only list_by_tenant exercised)
from __future__ import annotations

import uuid

import pytest

from src.api.mcp.server import AGENT_TOKEN_HEADER
from src.foundation.ai.gateway.adapters.postgres_token_repository import (
    PostgresAgentTokenRepository,
)
from src.foundation.ai.gateway.domain.token_rules import Scope
from src.foundation.research_data.adapters.postgres_repository import PostgresResearchRepository
from tests.api.mcp.conftest import issue
from tests.api.mcp.test_tools_research_data import (
    SOURCE_ID,
    _insert_source_contract,
    _item,
    _make_tenant,
    _register_source,
)
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async


@pytest.fixture
def research_repo(pool) -> PostgresResearchRepository:  # noqa: ANN001
    return PostgresResearchRepository(pool)


_URL = "/mcp/tools/research_data_search"
_BUDGET_MS = 3000.0


async def _seeded_token(pool, token_repo, research_repo, n_items: int = 1):  # noqa: ANN001
    await _insert_source_contract(pool)
    tenant_id = await _make_tenant(pool)
    await _register_source(research_repo)
    for _ in range(n_items):
        await research_repo.append_item(tenant_id, _item(), external_id=f"ext-{uuid.uuid4().hex}")
    return await issue(token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.READ}))


# -- negative ------------------------------------------------------------------


async def test_search_missing_source_id_is_422(client, token_repo: PostgresAgentTokenRepository):
    issued = await issue(token_repo, tenant_id=uuid.uuid4(), scopes=frozenset({Scope.READ}))
    response = await client.post(_URL, json={}, headers={AGENT_TOKEN_HEADER: issued.secret})
    assert response.status_code == 422


async def test_search_without_any_credentials_is_401(client):
    response = await client.post(_URL, json={"source_id": SOURCE_ID})
    assert response.status_code == 401


async def test_search_bogus_agent_token_is_401(client):
    response = await client.post(
        _URL, json={"source_id": SOURCE_ID}, headers={AGENT_TOKEN_HEADER: "bogus-secret"}
    )
    assert response.status_code == 401


async def test_search_malformed_as_of_is_422(client, token_repo: PostgresAgentTokenRepository):
    issued = await issue(token_repo, tenant_id=uuid.uuid4(), scopes=frozenset({Scope.READ}))
    response = await client.post(
        _URL,
        json={"source_id": SOURCE_ID, "as_of": "not-a-datetime"},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 422


async def test_search_kind_filter_without_match_returns_empty(
    client, pool, token_repo: PostgresAgentTokenRepository, research_repo
):
    issued = await _seeded_token(pool, token_repo, research_repo)
    response = await client.post(
        _URL,
        json={"source_id": SOURCE_ID, "kinds": ["no-such-kind"]},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 200, response.text
    assert response.json() == []


async def test_search_source_not_in_contract_returns_403(
    client, pool, token_repo: PostgresAgentTokenRepository, research_repo
):
    """A source_id that has no source_contract row must be rejected with 403 (fail-closed)."""
    tenant_id = await _make_tenant(pool)
    await _register_source(research_repo)
    # insert an item under this tenant but NO source_contract row
    await research_repo.append_item(tenant_id, _item(), external_id=f"ext-{uuid.uuid4().hex}")
    issued = await issue(token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.READ}))
    response = await client.post(
        _URL,
        json={"source_id": "NO_SUCH_SOURCE"},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 403, (
        "source_contract이 없는 source_id는 403을 반환해야 하며 "
        "빈 목록으로 조용히 반환되어서는 안 된다"
    )


async def test_search_cross_tenant_isolation(
    client, pool, token_repo: PostgresAgentTokenRepository, research_repo
):
    """Items indexed under tenant A must not leak when searched with tenant B's token."""
    await _insert_source_contract(pool)
    await _register_source(research_repo)
    tenant_a = await _make_tenant(pool)
    tenant_b = await _make_tenant(pool)
    # seed items under tenant A only
    await research_repo.append_item(tenant_a, _item(), external_id=f"ext-{uuid.uuid4().hex}")
    token_b = await issue(token_repo, tenant_id=tenant_b, scopes=frozenset({Scope.READ}))
    response = await client.post(
        _URL,
        json={"source_id": SOURCE_ID},
        headers={AGENT_TOKEN_HEADER: token_b.secret},
    )
    assert response.status_code == 200, response.text
    assert response.json() == [], "tenant B의 토큰으로 tenant A의 항목이 조회되어서는 안 된다"


# -- failure injection ----------------------------------------------------------


async def test_search_entitlement_check_failure_is_500_not_empty(
    client,
    pool,
    token_repo: PostgresAgentTokenRepository,
    research_repo,
    monkeypatch: pytest.MonkeyPatch,
):
    """An unexpected error inside the entitlement check must fail closed (500)."""
    import src.api.mcp.tools_research_data as tools_research_data

    issued = await _seeded_token(pool, token_repo, research_repo)

    async def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise ConnectionError("simulated source_contract outage")

    monkeypatch.setattr(tools_research_data, "authorize_source_read", _boom)
    response = await client.post(
        _URL, json={"source_id": SOURCE_ID}, headers={AGENT_TOKEN_HEADER: issued.secret}
    )
    assert response.status_code == 500


# -- performance + gate-red -----------------------------------------------------


@pytest.mark.perf
async def test_search_best_of_wall_within_budget(
    client, pool, token_repo: PostgresAgentTokenRepository, research_repo, perf_budget
):
    issued = await _seeded_token(pool, token_repo, research_repo, n_items=20)
    reset_virtual_totals()

    def call():
        return client.post(
            _URL, json={"source_id": SOURCE_ID}, headers={AGENT_TOKEN_HEADER: issued.secret}
        )

    sample = await perf_budget.best_of_async(call, n=5, warmup=1)
    assert sample.result.status_code == 200, sample.result.text
    assert len(sample.result.json()) == 20
    assert sample.wall_ms < _BUDGET_MS, perf_budget.describe(sample, budget_ms=_BUDGET_MS)


@pytest.mark.perf
async def test_gate_red_virtual_delay_10x_breaches_budget(
    client,
    pool,
    token_repo: PostgresAgentTokenRepository,
    research_repo,
    perf_budget,
    monkeypatch: pytest.MonkeyPatch,
):
    """A virtual delay of 10x the budget must turn the budget assertion red."""
    import src.api.mcp.tools_research_data as tools_research_data

    issued = await _seeded_token(pool, token_repo, research_repo, n_items=20)
    monkeypatch.setattr(
        tools_research_data,
        "authorize_source_read",
        virtual_delayed_async(tools_research_data.authorize_source_read, _BUDGET_MS * 10 / 1000),
    )
    reset_virtual_totals()

    def call():
        return client.post(
            _URL, json={"source_id": SOURCE_ID}, headers={AGENT_TOKEN_HEADER: issued.secret}
        )

    sample = await perf_budget.best_of_async(call, n=3, warmup=1)
    try:
        assert sample.result.status_code == 200, sample.result.text
        with pytest.raises(AssertionError):
            assert sample.wall_ms < _BUDGET_MS, perf_budget.describe(sample, budget_ms=_BUDGET_MS)
    finally:
        reset_virtual_totals()
