"""RD-16 -- `research_data_search` MCP tool thin-proxy proof + adversarial
tests (real `TEST_DATABASE_URL`).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-16
("MCP `research_data_search` 도구(얇은 프록시) + 적대적 테스트", DoD "I-08, PIT
유지"). Same fixture shape as `tests/integration/api/test_research_data_router.py`
(RD-8) -- this file proves the MCP transport reaches the same authorization
and PIT-filter code, not a second implementation of either.

`source_contract` rows are inserted directly (same technique
`tests/integration/foundation/market_data/test_source_contract_repository.py`
uses) rather than faked via dependency override -- `create_mcp_app()` has no
`get_source_contract_repository` seam the way `src/api/routers/research_data.py`
does (`tools_research_data.py`'s module docstring: it constructs
`PostgresSourceContractRepository()` directly from `request.app.state.pool`,
mirroring the router's own default wiring).
"""

# ratchet-allow: get_item not called by research_data_search (only list_by_tenant exercised)
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.api.mcp.server import AGENT_TOKEN_HEADER
from src.foundation.ai.gateway.adapters.postgres_token_repository import (
    PostgresAgentTokenRepository,
)
from src.foundation.ai.gateway.domain.token_rules import Scope
from src.foundation.research_data.adapters.postgres_repository import PostgresResearchRepository
from src.foundation.research_data.contracts.v1 import ResearchItem, SourceMeta
from tests.api.mcp.conftest import issue

SOURCE_ID = "OPENDART"
_NOW = datetime.now(UTC)


async def _insert_source_contract(
    pool, *, source_id: str = SOURCE_ID, redistribution_scope: str = "DISPLAY"
) -> None:
    await pool.execute(
        """
        INSERT INTO source_contract
            (source_id, tier, credential_ref, redistribution_scope,
             rate_limit, quota, valid_from, valid_to, capability)
        VALUES ($1, 'ENTERPRISE', 'vault:test:v1', $2, 1000, 1000000, $3, NULL, $4)
        ON CONFLICT (source_id) DO UPDATE SET
            redistribution_scope = EXCLUDED.redistribution_scope,
            capability = EXCLUDED.capability
        """,
        source_id,
        redistribution_scope,
        _NOW - timedelta(days=365),
        json.dumps(
            {"asset_classes": ["EQUITY"], "resolutions": ["1d"], "corporate_actions": False}
        ),
    )


def _item(*, source_id: str = SOURCE_ID, known_at: datetime | None = None) -> ResearchItem:
    now = known_at if known_at is not None else _NOW
    return ResearchItem(
        item_id=uuid.uuid4(),
        source_id=source_id,
        kind="filing",
        published_at=now,
        known_at=now,
        instruments=("005930",),
        title="Test filing",
        body_ref=None,
        url="https://example.invalid/filing/1",
        language="ko",
        hash="a" * 64,
        revision_of=None,
    )


async def _register_source(
    research_repo: PostgresResearchRepository, *, source_id: str = SOURCE_ID
) -> None:
    await research_repo.upsert_source(
        SourceMeta(
            source_id=source_id,
            publisher="Test Publisher",
            redistribution="store_full",
            license_ref="https://example.invalid/license",
            rate_limit=60,
            coverage="2020-01-01~present",
        )
    )


async def _make_tenant(pool) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    await pool.execute("INSERT INTO tenant (id, kind) VALUES ($1, 'PERSONAL')", tenant_id)
    return tenant_id


@pytest.fixture
def research_repo(pool) -> PostgresResearchRepository:  # noqa: ANN001
    return PostgresResearchRepository(pool)


# --- positive: 위임 증명 (RD-7 search()와 동일 결과) ---


async def test_research_data_search_tool_returns_seeded_item(
    client,
    pool,
    token_repo: PostgresAgentTokenRepository,
    research_repo: PostgresResearchRepository,
):
    await _insert_source_contract(pool)
    tenant_id = await _make_tenant(pool)
    await _register_source(research_repo)
    item = _item()
    await research_repo.append_item(tenant_id, item, external_id=f"ext-{uuid.uuid4().hex}")
    issued = await issue(token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.READ}))

    response = await client.post(
        "/mcp/tools/research_data_search",
        json={"source_id": SOURCE_ID},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_id"] for row in body] == [str(item.item_id)]


# --- negative 1: 등록되지 않은(계약 없는) 소스는 403 ---


async def test_research_data_search_tool_unregistered_source_is_403(
    client, token_repo: PostgresAgentTokenRepository
):
    unknown_source = f"NO_CONTRACT_{uuid.uuid4().hex}"
    issued = await issue(token_repo, tenant_id=uuid.uuid4(), scopes=frozenset({Scope.READ}))

    response = await client.post(
        "/mcp/tools/research_data_search",
        json={"source_id": unknown_source},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 403


# --- negative 2: INTERNAL 스코프 계약은 USER_OWN_DISPLAY 용도를 거부한다 ---


async def test_research_data_search_tool_internal_scope_source_denies(
    client, pool, token_repo: PostgresAgentTokenRepository
):
    source_id = f"INTERNAL_ONLY_{uuid.uuid4().hex}"
    await _insert_source_contract(pool, source_id=source_id, redistribution_scope="INTERNAL")
    issued = await issue(token_repo, tenant_id=uuid.uuid4(), scopes=frozenset({Scope.READ}))

    response = await client.post(
        "/mcp/tools/research_data_search",
        json={"source_id": source_id},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 403


# --- negative 3: 교차 테넌트 -- 다른 테넌트 항목은 검색 결과에 나타나지 않는다 ---


async def test_research_data_search_tool_cross_tenant_item_not_returned(
    client,
    pool,
    token_repo: PostgresAgentTokenRepository,
    research_repo: PostgresResearchRepository,
):
    await _insert_source_contract(pool)
    owner_tenant = await _make_tenant(pool)
    await _register_source(research_repo)
    item = _item()
    await research_repo.append_item(owner_tenant, item, external_id=f"ext-{uuid.uuid4().hex}")

    attacker_tenant = await _make_tenant(pool)
    issued = await issue(token_repo, tenant_id=attacker_tenant, scopes=frozenset({Scope.READ}))

    response = await client.post(
        "/mcp/tools/research_data_search",
        json={"source_id": SOURCE_ID},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 200, response.text
    assert response.json() == []


# --- negative 4 (인간 JWT 거부, I-08/AI-15 재확인): Authorization 헤더는 거부 ---


async def test_research_data_search_tool_rejects_human_jwt(client):
    response = await client.post(
        "/mcp/tools/research_data_search",
        json={"source_id": SOURCE_ID},
        headers={"Authorization": "Bearer not-an-agent-token"},
    )
    assert response.status_code == 401


# --- RD-A1 PIT: as_of보다 known_at이 미래인 항목은 반환되지 않는다 ---


async def test_research_data_search_tool_excludes_future_known_at(
    client,
    pool,
    token_repo: PostgresAgentTokenRepository,
    research_repo: PostgresResearchRepository,
):
    await _insert_source_contract(pool)
    tenant_id = await _make_tenant(pool)
    await _register_source(research_repo)
    past_item = _item(known_at=_NOW - timedelta(days=1))
    future_item = _item(known_at=_NOW + timedelta(days=365))
    await research_repo.append_item(tenant_id, past_item, external_id=f"ext-{uuid.uuid4().hex}")
    await research_repo.append_item(tenant_id, future_item, external_id=f"ext-{uuid.uuid4().hex}")
    issued = await issue(token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.READ}))

    response = await client.post(
        "/mcp/tools/research_data_search",
        json={"source_id": SOURCE_ID, "as_of": _NOW.isoformat()},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 200, response.text
    ids = {row["item_id"] for row in response.json()}
    assert ids == {str(past_item.item_id)}, (
        "RD-A1: known_at > as_of인 항목은 어떤 경로로도 노출되면 안 된다"
    )


# --- D2 numeric perf assertion ---


@pytest.mark.perf
async def test_research_data_search_tool_latency_stays_within_normalized_ceiling(
    client,
    pool,
    token_repo: PostgresAgentTokenRepository,
    research_repo: PostgresResearchRepository,
    perf_budget,
):
    await _insert_source_contract(pool)
    tenant_id = await _make_tenant(pool)
    await _register_source(research_repo)
    seeded_item = _item()
    await research_repo.append_item(tenant_id, seeded_item, external_id=f"ext-{uuid.uuid4().hex}")
    for _ in range(50):
        await research_repo.append_item(tenant_id, _item(), external_id=f"ext-{uuid.uuid4().hex}")
    issued = await issue(token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.READ}))

    baseline_sample = await perf_budget.sample_async(
        lambda: client.post(
            "/mcp/tools/research_data_search",
            json={"source_id": SOURCE_ID, "instruments": ["no-such-instrument"]},
            headers={AGENT_TOKEN_HEADER: issued.secret},
        ),
        batch=8,
    )
    baseline = baseline_sample.result
    assert baseline.status_code == 200, baseline.text
    baseline_elapsed_s = baseline_sample.wall_ms / 1000

    full_sample = await perf_budget.sample_async(
        lambda: client.post(
            "/mcp/tools/research_data_search",
            json={"source_id": SOURCE_ID},
            headers={AGENT_TOKEN_HEADER: issued.secret},
        ),
        batch=8,
    )
    full = full_sample.result
    assert full.status_code == 200, full.text
    assert len(full.json()) == 51

    full_elapsed_s = full_sample.wall_ms / 1000
    ceiling = baseline_elapsed_s * 20 + 0.5
    assert full_elapsed_s <= ceiling, (
        f"51개 항목 검색이 {full_elapsed_s:.3f}s 걸림 "
        f"(baseline {baseline_elapsed_s:.3f}s, 정규화 상한 {ceiling:.3f}s)"
    )


# --- D2 failure injection: 지연 주입 시 정상 단언이 적색으로 전환되어야 한다 ---


class _DelayingResearchRepository:
    """repository list_by_tenant에 지연을 주입해 latency budget 초과를 검증하는
    failure-injection용 repository."""

    def __init__(self, pool, delay_ms: float = 2000) -> None:  # noqa: ANN001
        self._pool = pool
        self._delay_ms = delay_ms

    async def get_item(self, tenant_id, item_id):  # noqa: ANN001
        raise NotImplementedError("not exercised by this tool")

    async def list_by_tenant(self, tenant_id, *, source_id, limit=200):  # noqa: ANN001
        import asyncio

        from src.foundation.research_data.adapters.postgres_repository import _row_to_item

        await asyncio.sleep(self._delay_ms / 1000)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM research_items WHERE tenant_id = $1 AND source_id = $2 "
                "ORDER BY known_at DESC, item_id DESC LIMIT $3",
                tenant_id,
                source_id,
                limit,
            )
        return [_row_to_item(row) for row in rows]


@pytest.mark.parametrize("baseline_delay_s", [0, 0.2], ids=["normal", "loaded-baseline"])
async def test_research_data_search_tool_latency_budget_fails_under_delay_injection(
    client,
    pool,
    token_repo: PostgresAgentTokenRepository,
    research_repo: PostgresResearchRepository,
    perf_budget,
    monkeypatch: pytest.MonkeyPatch,
    baseline_delay_s,
):
    """failure injection — repository 호출에 지연을 주입하면 정규화 상한을
    초과해야 한다.

    지연 주입: _DelayingResearchRepository.list_by_tenant를 monkeypatch해
    각 요청에 측정된 상한보다 2s 긴 지연을 추가. baseline(지연 없음) 대비 full(지연 주입)이
    ceiling(baseline*20+0.5)를 초과해야 한다.

    monkeypatch.setattr 사용 — xdist 워커 간/내 모듈 레벨 뱅킹을 pytest가
    자동 복구하므로 try/finally 누락 시 다른 테스트가 지연에 영구 노출되는
    결함을 방지한다(task-20480).
    """
    import asyncio

    import src.api.mcp.tools_research_data as tools_research_data

    await _insert_source_contract(pool)
    tenant_id = await _make_tenant(pool)
    await _register_source(research_repo)
    seeded_item = _item()
    await research_repo.append_item(tenant_id, seeded_item, external_id=f"ext-{uuid.uuid4().hex}")
    for _ in range(50):
        await research_repo.append_item(tenant_id, _item(), external_id=f"ext-{uuid.uuid4().hex}")
    issued = await issue(token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.READ}))

    async def baseline_request():
        # 재현: baseline만 느려져도 고정 2s 장애는 정규화 상한 아래로 내려간다.
        await asyncio.sleep(baseline_delay_s)
        return await client.post(
            "/mcp/tools/research_data_search",
            json={"source_id": SOURCE_ID, "instruments": ["no-such-instrument"]},
            headers={AGENT_TOKEN_HEADER: issued.secret},
        )

    baseline_sample = await perf_budget.sample_async(baseline_request, batch=1)
    assert baseline_sample.result.status_code == 200, baseline_sample.result.text
    assert baseline_sample.result.json() == []
    baseline_elapsed_s = baseline_sample.wall_ms / 1000

    ceiling = baseline_elapsed_s * 20 + 0.5
    # 예산은 유지하고 장애 주입량만 실제 예산을 확실히 넘도록 보정한다.
    monkeypatch.setattr(
        tools_research_data,
        "PostgresResearchRepository",
        lambda p: _DelayingResearchRepository(p, delay_ms=(ceiling + 2) * 1000),
    )
    full_sample = await perf_budget.sample_async(
        lambda: client.post(
            "/mcp/tools/research_data_search",
            json={"source_id": SOURCE_ID},
            headers={AGENT_TOKEN_HEADER: issued.secret},
        ),
        batch=1,
    )
    full_elapsed_s = full_sample.wall_ms / 1000
    assert full_sample.result.status_code == 200, full_sample.result.text
    assert len(full_sample.result.json()) == 51

    assert full_elapsed_s > ceiling, (
        f"지연 주입에도 full_elapsed_s={full_elapsed_s:.3f}s <= ceiling={ceiling:.3f}s — "
        "지연이 예산 단언을 포착하지 못함"
    )


# --- D2 gate-red reproduction: repository가 tenant_id를 무시하면 교차 테넌트가 샌다 ---


class _LeakyResearchRepository:
    def __init__(self, pool) -> None:  # noqa: ANN001
        self._pool = pool

    async def get_item(self, tenant_id, item_id):  # noqa: ANN001
        raise NotImplementedError("not exercised by this tool")

    async def list_by_tenant(self, tenant_id, *, source_id, limit=200):  # noqa: ANN001
        from src.foundation.research_data.adapters.postgres_repository import _row_to_item

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM research_items WHERE source_id = $1 "
                "ORDER BY known_at DESC, item_id DESC LIMIT $2",
                source_id,
                limit,
            )
        return [_row_to_item(row) for row in rows]


async def test_research_data_search_tool_gate_red_if_repository_ignores_tenant(
    client,
    pool,
    token_repo: PostgresAgentTokenRepository,
    research_repo: PostgresResearchRepository,
    monkeypatch: pytest.MonkeyPatch,
):
    import src.api.mcp.tools_research_data as tools_research_data

    await _insert_source_contract(pool)
    owner_tenant = await _make_tenant(pool)
    await _register_source(research_repo)
    item = _item()
    await research_repo.append_item(owner_tenant, item, external_id=f"ext-{uuid.uuid4().hex}")

    attacker_tenant = await _make_tenant(pool)
    issued = await issue(token_repo, tenant_id=attacker_tenant, scopes=frozenset({Scope.READ}))

    monkeypatch.setattr(
        tools_research_data,
        "PostgresResearchRepository",
        lambda p: _LeakyResearchRepository(p),
    )
    leaked = await client.post(
        "/mcp/tools/research_data_search",
        json={"source_id": SOURCE_ID},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )

    assert leaked.status_code == 200, leaked.text
    assert str(item.item_id) in {row["item_id"] for row in leaked.json()}, (
        "list_by_tenant가 tenant_id를 무시하면 MCP 경로도 교차 테넌트 검색을 노출한다"
    )


# --- D2 failure injection: repository 의존성 예외는 500 전파(조용히 삼키지 않는다) ---


class _RaisingResearchRepository:
    def __init__(self, pool) -> None:  # noqa: ANN001
        self._pool = pool

    async def list_by_tenant(self, tenant_id, *, source_id, limit=200):  # noqa: ANN001
        raise ConnectionError("simulated research_items connection loss")


async def test_research_data_search_tool_propagates_repository_failure(
    client,
    pool,
    token_repo: PostgresAgentTokenRepository,
    monkeypatch: pytest.MonkeyPatch,
):
    import src.api.mcp.tools_research_data as tools_research_data

    await _insert_source_contract(pool)
    tenant_id = await _make_tenant(pool)
    issued = await issue(token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.READ}))

    monkeypatch.setattr(
        tools_research_data,
        "PostgresResearchRepository",
        lambda p: _RaisingResearchRepository(p),
    )
    response = await client.post(
        "/mcp/tools/research_data_search",
        json={"source_id": SOURCE_ID},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )

    assert response.status_code == 500, (
        "repository 의존성 예외(ConnectionError)는 fail-closed로 500 전파되어야 하며 "
        "200/빈 목록으로 조용히 삼켜지면 안 된다"
    )
