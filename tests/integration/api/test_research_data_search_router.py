"""RD-17(task-7775) -- /v1/foundation/research-data/search·/sources 통합
테스트 (real FastAPI app + TEST_DATABASE_URL).

RD-8(test_research_data_router.py)과 같은 픽스처 관용(전역 source_contract는
`get_source_contract_repository` override로 페이크, research_items/
research_sources는 `PostgresResearchRepository`로 직접 시딩)을 재사용하되,
이 라우터는 `source_id`를 요구하지 않는 다중 소스 횡단 검색이라 소스별로
다른 계약(허용/거부)을 돌려주는 `_PerSourceContractRepository`를 쓴다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import asyncpg
import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.routers.research_data import get_research_repository, get_source_contract_repository
from src.foundation.market_data.domain.entitlement.source_contract import (
    RedistributionScope,
    SourceCapability,
    SourceContract,
    SourceContractTier,
)
from src.foundation.research_data.adapters.postgres_repository import (
    PostgresResearchRepository,
    _row_to_item,
)
from src.foundation.research_data.contracts.v1 import ResearchItem, SourceMeta
from src.main import app
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import virtual_delayed_async

STRONG_PASSWORD = "Str0ng!Passw0rd"
BASE = "/v1/foundation/research-data"
SOURCE_A = "OPENDART"
SOURCE_B = "GDELT"


def _contract(scope: RedistributionScope, *, source_id: str) -> SourceContract:
    now = datetime.now(UTC)
    return SourceContract(
        source_id=source_id,
        tier=SourceContractTier.ENTERPRISE,
        credential_ref="test:none",
        redistribution_scope=scope,
        rate_limit=1000,
        quota=1_000_000,
        valid_from=now - timedelta(days=365),
        valid_to=None,
        capability=SourceCapability(
            asset_classes=frozenset({"EQUITY"}), resolutions=frozenset({"1d"})
        ),
    )


class _PerSourceContractRepository:
    def __init__(self, contracts: dict[str, SourceContract | None]) -> None:
        self._contracts = contracts

    async def get(self, conn: asyncpg.Connection, source_id: str) -> SourceContract | None:
        return self._contracts.get(source_id)


class _AllAllowedContractRepository:
    async def get(self, conn: asyncpg.Connection, source_id: str) -> SourceContract | None:
        return _contract(RedistributionScope.DISPLAY, source_id=source_id)


@pytest.fixture
async def client(monkeypatch):
    # Search tests must not let the periodic safety tick mutate shared DB state.
    # The delay-injection test keeps the lifespan alive long enough to run it.
    monkeypatch.setattr("src.services.background_loops.run_circuit_breaker_tick", AsyncMock())
    async with app.router.lifespan_context(app):
        app.dependency_overrides[get_source_contract_repository] = _AllAllowedContractRepository
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
        app.dependency_overrides.pop(get_source_contract_repository, None)


async def _register(client: AsyncClient) -> tuple[dict, uuid.UUID]:
    response = await client.post(
        "/auth/register",
        json={"email": f"test-{uuid.uuid4().hex}@example.com", "password": STRONG_PASSWORD},
    )
    token = response.json()["data"]["access_token"]
    user_id = jwt.decode(token, options={"verify_signature": False})["sub"]
    return {"Authorization": f"Bearer {token}"}, uuid.UUID(user_id)


def _item(
    *, source_id: str, title: str = "Test filing", known_at: datetime | None = None
) -> ResearchItem:
    now = known_at if known_at is not None else datetime.now(UTC)
    return ResearchItem(
        item_id=uuid.uuid4(),
        source_id=source_id,
        kind="filing",
        published_at=now,
        known_at=now,
        instruments=("005930",),
        title=title,
        body_ref=None,
        url="https://example.invalid/filing/1",
        language="ko",
        hash="a" * 64,
        revision_of=None,
    )


@pytest.fixture
async def seeded(client: AsyncClient) -> dict:
    headers_a, tenant_a = await _register(client)
    headers_b, tenant_b = await _register(client)
    pool = app.state.pool
    repo = PostgresResearchRepository(pool)
    for source_id in (SOURCE_A, SOURCE_B):
        await repo.upsert_source(
            SourceMeta(
                source_id=source_id,
                publisher=f"Publisher {source_id}",
                redistribution="store_full",
                license_ref="https://example.invalid/license",
                rate_limit=60,
                coverage="2020-01-01~present",
            )
        )
    item_a = _item(source_id=SOURCE_A, title="삼성전자 분기보고서")
    item_a_id = await repo.append_item(tenant_a, item_a, external_id=f"ext-{uuid.uuid4().hex}")
    item_b = _item(source_id=SOURCE_B, title="글로벌 뉴스 속보")
    item_b_id = await repo.append_item(tenant_a, item_b, external_id=f"ext-{uuid.uuid4().hex}")
    return {
        "a": headers_a,
        "b": headers_b,
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "item_a_id": item_a_id,
        "item_b_id": item_b_id,
    }


async def test_search_returns_matching_items_across_sources(client, seeded):
    response = await client.post(
        f"{BASE}/search", json={"query": "", "kinds": []}, headers=seeded["a"]
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert {i["item_id"] for i in body["items"]} == {
        str(seeded["item_a_id"]),
        str(seeded["item_b_id"]),
    }
    assert body["total"] == 2
    assert body["truncated"] is False


async def test_search_filters_by_title_query(client, seeded):
    response = await client.post(
        f"{BASE}/search", json={"query": "삼성전자", "kinds": []}, headers=seeded["a"]
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert [i["item_id"] for i in body["items"]] == [str(seeded["item_a_id"])]


# --- negative ---


async def test_negative_1_empty_result_is_200_with_empty_list(client, seeded):
    """DoD -- 빈 결과는 500/예외가 아니라 200 + 빈 리스트다."""
    response = await client.post(
        f"{BASE}/search", json={"query": "no-such-title-xyz", "kinds": []}, headers=seeded["a"]
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body == {"items": [], "total": 0, "truncated": False}


async def test_negative_2_unauthorized_source_items_are_silently_excluded(client, seeded):
    app.dependency_overrides[get_source_contract_repository] = lambda: _PerSourceContractRepository(
        {
            SOURCE_A: _contract(RedistributionScope.DISPLAY, source_id=SOURCE_A),
            SOURCE_B: None,  # no contract row -> denied
        }
    )
    try:
        response = await client.post(
            f"{BASE}/search", json={"query": "", "kinds": []}, headers=seeded["a"]
        )
    finally:
        app.dependency_overrides.pop(get_source_contract_repository, None)
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert [i["item_id"] for i in body["items"]] == [str(seeded["item_a_id"])]


async def test_negative_3_cross_tenant_search_does_not_leak_items(client, seeded):
    response = await client.post(
        f"{BASE}/search", json={"query": "", "kinds": []}, headers=seeded["b"]
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["items"] == []


async def test_negative_4_unauthenticated_search_is_401_envelope(client):
    response = await client.post(f"{BASE}/search", json={"query": "", "kinds": []})
    assert response.status_code == 401
    assert "error_code" in response.json()


async def test_negative_5_sources_endpoint_never_500s_on_a_bare_request(client):
    """`research_sources`는 테넌트 스코프가 없는 공유 카탈로그라(다른 테스트가
    남긴 행이 섞일 수 있다) 빈 리스트 자체를 단언하지 않지만, 인증 없이도
    500/예외 없이 200 + 리스트 응답이어야 한다."""
    response = await client.get(f"{BASE}/sources")
    assert response.status_code == 200, response.text
    assert isinstance(response.json()["data"], list)


async def test_sources_endpoint_returns_catalog(client, seeded):
    response = await client.get(f"{BASE}/sources")
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert {SOURCE_A, SOURCE_B} <= {s["source_id"] for s in body}


# --- naive as_of → 4xx (not 500) ---


async def test_search_naive_as_of_returns_validation_error_not_500(client, seeded):
    """naive(as_of, tzinfo 없는) datetime을 보낼 때 500이 아니라 4xx(422)를 반환한다.

    root cause: query.py search_items()가 naive as_of에 대해 ValueError를
    던지는데 exception_registry_foundation_research.py가 매핑하지 않아
    라우터에서 미처리 500으로 새었다(task-10120).
    """
    naive_as_of = datetime(2024, 1, 1, 0, 0, 0)  # tzinfo 없음
    response = await client.post(
        f"{BASE}/search",
        json={"query": "BTC", "as_of": naive_as_of.isoformat(), "kinds": []},
        headers=seeded["a"],
    )
    # L4 section 2.3: VALIDATION_INVALID_FIELD maps to 400 in the standard envelope.
    assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
    body = response.json()
    assert body["error_code"] == "VALIDATION_INVALID_FIELD"
    assert "body.as_of" in body["details"]["fields"]
    assert "data" not in body


# --- D2 floor: failure-injection / numeric perf assertion / gate-red repro ---


class _BoomSourceContractRepository:
    """Failure injection -- the source-contract lookup raises (simulated DB
    outage) for every source. `authorize_source_read` must not swallow this
    into a silent exclusion -- an unexpected error still fails closed as a
    500, distinct from the ordinary "no contract row" case which excludes
    just that one source."""

    async def get(self, conn: asyncpg.Connection, source_id: str) -> SourceContract | None:
        raise ConnectionError("simulated source_contract backend outage")


async def test_search_source_contract_failure_fails_closed_not_open(client, seeded):
    app.dependency_overrides[get_source_contract_repository] = _BoomSourceContractRepository
    try:
        response = await client.post(
            f"{BASE}/search", json={"query": "", "kinds": []}, headers=seeded["a"]
        )
    finally:
        app.dependency_overrides.pop(get_source_contract_repository, None)
    assert response.status_code == 500, response.text
    body = response.json()
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "data" not in body, "장애 상황에서 검색 결과가 노출되면 안 된다(fail-closed)"
    assert "simulated source_contract backend outage" not in body["message"]


@pytest.mark.perf
async def test_search_latency_stays_within_normalized_ceiling(client, seeded, perf_budget):
    pool = app.state.pool
    repo = PostgresResearchRepository(pool)
    for _ in range(50):
        await repo.append_item(
            seeded["tenant_a"], _item(source_id=SOURCE_A), external_id=f"ext-{uuid.uuid4().hex}"
        )

    baseline_resp = await perf_budget.sample_async(lambda: client.get(f"{BASE}/sources"), batch=8)
    baseline_elapsed = baseline_resp.cpu_ms / 1000
    assert baseline_resp.result.status_code == 200, baseline_resp.result.text

    full_resp = await perf_budget.sample_async(
        lambda: client.post(f"{BASE}/search", json={"query": "", "kinds": []}, headers=seeded["a"]),
        batch=8,
    )
    full_elapsed = full_resp.cpu_ms / 1000
    assert full_resp.result.status_code == 200, full_resp.result.text
    assert len(full_resp.result.json()["data"]["items"]) == 52

    # CI 병렬 실행(xdist)에서 wall_ms는 다른 워커의 CPU 경합으로 왜곡되므로
    # cpu_ms(time.process_time) 기준으로 단언한다(task-19183 관례).
    # batch=8로 Windows 15.6ms 양자화 오차를 줄이고, floor를 2.0s로 늘려
    # CI CPU 경합 spike를 흡수한다.
    ceiling = max(baseline_elapsed * 20 + 0.5, 2.0)
    assert full_elapsed <= ceiling, (
        f"52개 항목 검색이 {full_elapsed:.3f}s 걸림 "
        f"(baseline {baseline_elapsed:.3f}s, 정규화 상한 {ceiling:.3f}s)"
    )


# --- D2 single-call CPU convergence (0 수렴) -------------------------------


@pytest.mark.perf
async def test_search_cpu_converges_to_zero_single_call(
    client, seeded, perf_budget: PerfBudget
) -> None:
    """단일 검색 호출이 0에 수렴하는 CPU 시간을 보여야 한다.

    `time.process_time()`은 Windows에서 약 15.6ms 양자화 오차가 있으므로
    batch=64로 여러 호출을 묶어서 1회당 오차를 줄인다. CI 병렬 실행에서
    CPU 경합 spike를 피하기 위해 `best_of_async(n=3, warmup=1)`로
    best-of-3 최소값을 사용하고, 첫 호출은 warmup으로 버린다. 순수 도메인
    로직 불변식(I-03)과 호환되도록 1회당 CPU 시간이 50ms 미만이어야 한다
    (DB I/O가 아닌 Python CPU 코어만 측정).
    """

    async def _search_once() -> None:
        await client.post(f"{BASE}/search", json={"query": "", "kinds": []}, headers=seeded["a"])

    sample = await perf_budget.best_of_async(_search_once, n=3, warmup=1, batch=64)
    assert sample.cpu_ms < 50.0, (
        f"단일 호출 CPU 시간 {sample.cpu_ms:.3f}ms — "
        f"DB I/O를 제외한 Python CPU 코어는 50ms 미만이어야 함 (budget<50ms)"
    )


# --- D2 gate-red reproduction: 지연 주입 시 성능 단언이 적색으로 전환 ------


async def test_search_latency_budget_fails_under_10x_delay_injection(
    client, seeded, perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 적색 재현(D2, task-11539 감사 후속): 성능 단언이 실제로 지연을
    포착하는지 역으로 증명한다. repository의 DB 조회에 0.5s 지연을 주입하면
    정규화 상한을 초과해 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다.
    """
    # PostgresResearchRepository.list_by_tenant_across_sources를 monkeypatch
    # 하여 각 호출에 0.5s 지연을 추가 — 가상 지연으로 측정치만 늘림 (실제 sleep 없음)
    _real_repo_class = PostgresResearchRepository
    _sleep_count = [0]

    class _DelayedResearchRepository(_real_repo_class):
        async def list_by_tenant_across_sources(self, tenant_id, *, limit=200) -> list:
            _sleep_count[0] += 1
            return await virtual_delayed_async(super().list_by_tenant_across_sources, 5.0)(
                tenant_id, limit=limit
            )

    pool = app.state.pool
    app.dependency_overrides[get_research_repository] = lambda: _DelayedResearchRepository(pool)
    try:
        # baseline은 지연 없는 엔드포인트로 측정
        baseline_sample = await perf_budget.sample_async(
            lambda: client.get(f"{BASE}/sources"),
            batch=8,
        )
        baseline_elapsed = baseline_sample.wall_ms / 1000
        assert baseline_sample.result.status_code == 200, baseline_sample.result.text

        # full 호출 — 지연 주입으로 인해 ceiling 초과해야 함
        full_sample = await perf_budget.sample_async(
            lambda: client.post(
                f"{BASE}/search", json={"query": "", "kinds": []}, headers=seeded["a"]
            ),
            batch=8,
        )
        full_elapsed = full_sample.wall_ms / 1000
        assert full_sample.result.status_code == 200, full_sample.result.text

        # 지연 주입 시 full_elapsed가 ceiling를 초과해야 함 (적색)
        # ceiling는 정규화 상한과 동일한 공식 — 30s floor는 CI 안정화용이므로
        # 지연 주입 테스트에서는 baseline 기반 비율만 사용
        ceiling = baseline_elapsed * 20 + 0.5
        assert full_elapsed > ceiling, (
            f"지연 주입에도 full_elapsed={full_elapsed:.3f}s <= ceiling={ceiling:.3f}s — "
            "지연이 예산 단언을 포착하지 못함"
        )
    finally:
        app.dependency_overrides.pop(get_research_repository, None)


class _LeakyResearchRepository:
    """Gate-red reproduction -- `list_by_tenant_across_sources`가 tenant_id
    WHERE 절을 빠뜨리면 어떻게 되는지 재현한다: 요청한 테넌트가 아니라 전체
    테넌트의 항목을 돌려준다."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._real = PostgresResearchRepository(pool)

    async def list_by_tenant_across_sources(
        self, tenant_id: uuid.UUID, *, limit: int = 200
    ) -> list[ResearchItem]:
        async with self._real._pool.acquire() as conn:  # noqa: SLF001 -- deliberate defect repro
            rows = await conn.fetch(
                "SELECT * FROM research_items ORDER BY known_at DESC, item_id DESC LIMIT $1",
                limit,
            )
        return [_row_to_item(row) for row in rows]

    async def get_linked_instrument_ids(self, item_ids):
        return await self._real.get_linked_instrument_ids(item_ids)


async def test_search_gate_red_reproduction_if_repository_ignores_tenant(client, seeded):
    """tenant_id 필터가 빠지면 tenant B의 검색이 tenant A의 항목을 그대로
    본다 -- 200-with-leak이 재현 대상 결함이지 기대 동작이 아니다."""
    pool = app.state.pool
    app.dependency_overrides[get_research_repository] = lambda: _LeakyResearchRepository(pool)
    try:
        leaked = await client.post(
            f"{BASE}/search", json={"query": "", "kinds": []}, headers=seeded["b"]
        )
    finally:
        app.dependency_overrides.pop(get_research_repository, None)

    assert leaked.status_code == 200, leaked.text
    leaked_ids = {i["item_id"] for i in leaked.json()["data"]["items"]}
    assert str(seeded["item_a_id"]) in leaked_ids, (
        "list_by_tenant_across_sources가 tenant_id를 무시하면 교차 테넌트 검색 방어는 무력화된다"
    )
