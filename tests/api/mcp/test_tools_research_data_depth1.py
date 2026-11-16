"""RD-16 depth-1 supplement for `research_data_search` (D2 floor).

Adds negative tests x10 (6 domain-level + 4 auth/validation), failure injection x1,
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
from datetime import timedelta

import pytest

from src.api.mcp.server import AGENT_TOKEN_HEADER
from src.foundation.ai.gateway.adapters.postgres_token_repository import (
    PostgresAgentTokenRepository,
)
from src.foundation.ai.gateway.domain.token_rules import Scope
from src.foundation.research_data.adapters.postgres_repository import PostgresResearchRepository
from src.foundation.research_data.contracts.v1 import ResearchItem
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


# -- domain-level negative (additional D2 coverage) -----------------------------


async def test_search_instruments_filter_excludes_non_matching(
    client, pool, token_repo: PostgresAgentTokenRepository, research_repo
):
    """Items whose instruments do not match the filter must be excluded."""
    await _insert_source_contract(pool)
    tenant_id = await _make_tenant(pool)
    await _register_source(research_repo)
    # seed items for two different instruments (directly construct ResearchItem)
    item_a = ResearchItem(
        item_id=uuid.uuid4(),
        source_id=SOURCE_ID,
        kind="filing",
        published_at=_NOW,
        known_at=_NOW,
        instruments=("005930",),
        title="Test filing A",
        body_ref=None,
        url="https://example.invalid/filing/a",
        language="ko",
        hash="a" * 64,
        revision_of=None,
    )
    item_b = ResearchItem(
        item_id=uuid.uuid4(),
        source_id=SOURCE_ID,
        kind="filing",
        published_at=_NOW,
        known_at=_NOW,
        instruments=("000660",),
        title="Test filing B",
        body_ref=None,
        url="https://example.invalid/filing/b",
        language="ko",
        hash="b" * 64,
        revision_of=None,
    )
    await research_repo.append_item(tenant_id, item_a, external_id=f"ext-{uuid.uuid4().hex}")
    await research_repo.append_item(tenant_id, item_b, external_id=f"ext-{uuid.uuid4().hex}")
    issued = await issue(token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.READ}))
    response = await client.post(
        _URL,
        json={"source_id": SOURCE_ID, "instruments": ["005930"]},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 200, response.text
    ids = {row["item_id"] for row in response.json()}
    assert len(ids) == 1, "instruments 필터가 적용되면 매칭되지 않는 항목은 반환되지 않아야 한다"


async def test_search_published_date_range_filters_items(
    client, pool, token_repo: PostgresAgentTokenRepository, research_repo
):
    """published_from/published_to must restrict results to the given window."""
    await _insert_source_contract(pool)
    tenant_id = await _make_tenant(pool)
    await _register_source(research_repo)
    # Directly construct ResearchItem to control published_at
    old_item = ResearchItem(
        item_id=uuid.uuid4(),
        source_id=SOURCE_ID,
        kind="filing",
        published_at=_NOW - timedelta(days=365),
        known_at=_NOW - timedelta(days=365),
        instruments=("005930",),
        title="Old filing",
        body_ref=None,
        url="https://example.invalid/filing/old",
        language="ko",
        hash="c" * 64,
        revision_of=None,
    )
    recent_item = ResearchItem(
        item_id=uuid.uuid4(),
        source_id=SOURCE_ID,
        kind="filing",
        published_at=_NOW,
        known_at=_NOW,
        instruments=("005930",),
        title="Recent filing",
        body_ref=None,
        url="https://example.invalid/filing/recent",
        language="ko",
        hash="d" * 64,
        revision_of=None,
    )
    await research_repo.append_item(tenant_id, old_item, external_id=f"ext-{uuid.uuid4().hex}")
    await research_repo.append_item(tenant_id, recent_item, external_id=f"ext-{uuid.uuid4().hex}")
    issued = await issue(token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.READ}))
    response = await client.post(
        _URL,
        json={
            "source_id": SOURCE_ID,
            "published_from": (_NOW - timedelta(days=30)).isoformat(),
            "published_to": _NOW.isoformat(),
        },
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 200, response.text
    ids = {row["item_id"] for row in response.json()}
    assert str(recent_item.item_id) in ids, "published_date range 내에 있는 항목은 반환되어야 한다"
    assert str(old_item.item_id) not in ids, "published_date range 외부 항목은 반환되면 안 된다"


async def test_search_empty_kinds_returns_all_items(
    client, pool, token_repo: PostgresAgentTokenRepository, research_repo
):
    """An empty kinds filter (not None) must not exclude any items."""
    await _insert_source_contract(pool)
    tenant_id = await _make_tenant(pool)
    await _register_source(research_repo)
    for _i in range(3):
        await research_repo.append_item(tenant_id, _item(), external_id=f"ext-{uuid.uuid4().hex}")
    issued = await issue(token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.READ}))
    response = await client.post(
        _URL,
        json={"source_id": SOURCE_ID, "kinds": []},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 3, "kinds가 빈 배열이면 모든 항목이 반환되어야 한다 (0건이 아니어야 한다)"


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
