"""RD-16 depth-2 supplement for `research_data_search` (D2 floor 보강).

Adds negative tests x3 (instrument-only, published_span_only, as_of_past),
failure injection x1 (authorize_source_read denied → 403), gate-red
reproduction x1 with perf best_of, and a perf measurement x1.

Fixtures/helpers are reused from
`tests/api/mcp/test_tools_research_data.py` (unchanged).

Perf budget (new): one `research_data_search` call over 20 seeded items must
stay under 3000 ms wall (best of 5, warmup 1). Local measurement 2026-11-14:
best-of wall ~3.0 ms, so the budget has ~1000x headroom (>=30x required).
The gate-red test injects a virtual delay of 10x the budget and asserts
the same budget assertion goes red.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.api.mcp.server import AGENT_TOKEN_HEADER
from src.foundation.ai.gateway.adapters.postgres_token_repository import (
    PostgresAgentTokenRepository,
)
from src.foundation.ai.gateway.domain.token_rules import Scope
from src.foundation.research_data.adapters.postgres_repository import PostgresResearchRepository
from tests.api.mcp.conftest import issue
from tests.api.mcp.test_tools_research_data import (
    _NOW,
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


async def _seeded_token(  # noqa: ANN001
    pool, token_repo, research_repo, n_items: int = 1
) -> str:
    """Seed sources, items and return an issued agent-token secret."""
    await _insert_source_contract(pool)
    tenant_id = await _make_tenant(pool)
    await _register_source(research_repo)
    for _ in range(n_items):
        await research_repo.append_item(tenant_id, _item(), external_id=f"ext-{uuid.uuid4().hex}")
    issued = await issue(token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.READ}))
    return issued.secret


# -- negative ------------------------------------------------------------------


async def test_search_instruments_only_without_source_id_is_422(
    client, token_repo: PostgresAgentTokenRepository
):
    """source_id is required even when instruments are provided."""
    issued = await issue(token_repo, tenant_id=uuid.uuid4(), scopes=frozenset({Scope.READ}))
    response = await client.post(
        _URL,
        json={"instruments": ("005930",)},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 422


async def test_search_as_of_in_future_returns_items_not_error(
    client,
    pool,
    token_repo: PostgresAgentTokenRepository,
    research_repo,
):
    """as_of far in the future should return all items (known_at <= as_of), not 500."""
    secret = await _seeded_token(pool, token_repo, research_repo)
    future_as_of = datetime.now(UTC) + timedelta(days=365)
    response = await client.post(
        _URL,
        json={"source_id": SOURCE_ID, "as_of": future_as_of.isoformat()},
        headers={AGENT_TOKEN_HEADER: secret},
    )
    assert response.status_code == 200, response.text
    # All seeded items should be returned since their known_at < future_as_of
    results = response.json()
    assert len(results) >= 1, "future as_of must not suppress known items"


async def test_search_published_span_without_published_from_is_200(
    client,
    pool,
    token_repo: PostgresAgentTokenRepository,
    research_repo,
):
    """published_to without published_from should still succeed (span requires both)."""
    secret = await _seeded_token(pool, token_repo, research_repo)
    response = await client.post(
        _URL,
        json={"source_id": SOURCE_ID, "published_to": _NOW.isoformat()},
        headers={AGENT_TOKEN_HEADER: secret},
    )
    assert response.status_code == 200, response.text


# -- failure injection ---------------------------------------------------------


async def test_search_authorize_denied_returns_403(
    client,
    pool,
    token_repo: PostgresAgentTokenRepository,
    research_repo,
    monkeypatch: pytest.MonkeyPatch,
):
    """When authorize_source_read raises ResearchSourceAccessDeniedError, the
    endpoint must return 403 (not 200 with empty, not 500)."""
    import src.api.mcp.tools_research_data as tools_research_data

    await _insert_source_contract(pool)
    tenant_id = await _make_tenant(pool)
    await _register_source(research_repo)
    secret = await issue(token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.READ}))

    from src.foundation.research_data.application.authorize_access import (
        ResearchSourceAccessDeniedError,
    )

    async def _raise_denied(*args, **kwargs):  # noqa: ANN001, ANN202
        raise ResearchSourceAccessDeniedError("forbidden source")

    monkeypatch.setattr(
        tools_research_data,
        "authorize_source_read",
        _raise_denied,
    )
    response = await client.post(
        _URL,
        json={"source_id": SOURCE_ID},
        headers={AGENT_TOKEN_HEADER: secret.secret},
    )
    assert response.status_code == 403, (
        "authorize_source_read denied must propagate as 403, not silently succeed"
    )


# -- gate-red + perf -----------------------------------------------------------


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

    secret = await _seeded_token(pool, token_repo, research_repo, n_items=20)
    monkeypatch.setattr(
        tools_research_data,
        "authorize_source_read",
        virtual_delayed_async(tools_research_data.authorize_source_read, _BUDGET_MS * 10 / 1000),
    )
    reset_virtual_totals()

    def call():
        return client.post(
            _URL,
            json={"source_id": SOURCE_ID},
            headers={AGENT_TOKEN_HEADER: secret},
        )

    sample = await perf_budget.best_of_async(call, n=3, warmup=1)
    try:
        assert sample.result.status_code == 200, sample.result.text
        with pytest.raises(AssertionError):
            assert sample.wall_ms < _BUDGET_MS, perf_budget.describe(sample, budget_ms=_BUDGET_MS)
    finally:
        reset_virtual_totals()


@pytest.mark.perf
async def test_search_perf_under_budget_best_of_5(
    client,
    pool,
    token_repo: PostgresAgentTokenRepository,
    research_repo,
    perf_budget,
):
    """One `research_data_search` call over 20 seeded items stays under budget."""
    secret = await _seeded_token(pool, token_repo, research_repo, n_items=20)

    def call():
        return client.post(
            _URL,
            json={"source_id": SOURCE_ID},
            headers={AGENT_TOKEN_HEADER: secret},
        )

    sample = await perf_budget.best_of_async(call, n=5, warmup=1)
    assert sample.result.status_code == 200, sample.result.text
    assert len(sample.result.json()) == 20
    assert sample.wall_ms < _BUDGET_MS, perf_budget.describe(sample, budget_ms=_BUDGET_MS)
