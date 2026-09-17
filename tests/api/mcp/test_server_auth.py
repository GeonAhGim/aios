"""AI-15 -- `src/api/mcp/server.py` auth boundary.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §9 AI-15 DoD (I-08:
"the MCP/tool server holds no more authorization/logic than the REST layer
already enforces"), §3 ("a human JWT and X-AIOS-Agent-Token are separate
credential channels -- a request carrying both is rejected"), §8 adversarial
list ("human JWT accessing MCP" verbatim). ADR-2026-09-09-C D2: negative >=3,
failure injection 1, numeric performance assertion 1, gate-red reproduction 1.

Every test below drives the real ASGI app (`create_mcp_app`) over a real
`TEST_DATABASE_URL` token repository -- this is the boundary AI-4's
`authorize()` (DB round trip) and this leaf's `require_scope()` (header
policy) are jointly responsible for, so a fake token repository would not
exercise the actual SQL path a revoked/expired token depends on.
"""

from __future__ import annotations

import time
from datetime import timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.mcp.server import AGENT_TOKEN_HEADER, create_mcp_app
from src.foundation.ai.factory.application import research_tools
from src.foundation.ai.gateway.adapters.postgres_token_repository import (
    PostgresAgentTokenRepository,
)
from src.foundation.ai.gateway.application.revoke_token import revoke_token
from src.foundation.ai.gateway.domain.token_rules import Scope
from src.services.auth.tokens import TokenIssuer
from tests.api.mcp.conftest import NOW, issue

_INDICATOR_BODY = {
    "name": "SMA",
    "columns": {"close": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]},
    "params": {"timeperiod": 3},
}


@pytest.fixture
async def client(pool):  # noqa: ANN001 -- pytest fixture injection, type is asyncpg.Pool
    app = create_mcp_app(pool)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://mcp.test") as c:
        yield c


def _human_jwt() -> str:
    issuer = TokenIssuer.from_env()
    return issuer.issue_access(
        user_id=uuid4(),
        tenant_id=uuid4(),
        session_id=uuid4(),
        auth_level="MFA_VERIFIED",
        now=NOW,
    )


# --- 정상 경로: 유효한 agent 토큰은 read scope 도구에 닿는다 ---


async def test_valid_agent_token_reaches_the_tool(client, token_repo: PostgresAgentTokenRepository):
    issued = await issue(token_repo, tenant_id=uuid4(), scopes=frozenset({Scope.READ}))
    response = await client.post(
        "/mcp/tools/compute_indicator",
        json=_INDICATOR_BODY,
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 200
    assert response.json()["values"]["value"][2:] == [2.0, 3.0, 4.0, 5.0, 6.0, 7.0]


# --- 적대적 (§8 verbatim): 인간 JWT만으로는 MCP 도구에 닿지 못한다ㅡ ---


async def test_human_jwt_alone_is_rejected(client):
    """§8 "인간 JWT로 MCP 접근" -- 실제로 서명된 유효한 인간 세션 JWT를
    `Authorization: Bearer`로 보내도 (agent 토큰이 전혀 없어도) 거부된다.
    유효한 JWT조차 인가를 얻지 못한다는 것이 이 테스트의 요점 -- 서명·claims
    파싱 실패가 아니라 채널 자체가 다르다는 것을 증명한다."""
    response = await client.post(
        "/mcp/tools/compute_indicator",
        json=_INDICATOR_BODY,
        headers={"Authorization": f"Bearer {_human_jwt()}"},
    )
    assert response.status_code == 401
    assert "AI_TOKEN_REVOKED" in response.text


# --- negative #1: 인간 JWT + 유효한 agent 토큰을 동시에 보내도 거부(§3) ---


async def test_human_jwt_plus_valid_agent_token_is_still_rejected(
    client, token_repo: PostgresAgentTokenRepository
):
    issued = await issue(token_repo, tenant_id=uuid4(), scopes=frozenset({Scope.READ}))
    response = await client.post(
        "/mcp/tools/compute_indicator",
        json=_INDICATOR_BODY,
        headers={
            "Authorization": f"Bearer {_human_jwt()}",
            AGENT_TOKEN_HEADER: issued.secret,
        },
    )
    assert response.status_code == 401
    assert "AI_TOKEN_REVOKED" in response.text


# --- negative #2: 아무 자격증명도 없으면 거부 ---


async def test_missing_agent_token_is_rejected(client):
    response = await client.post("/mcp/tools/compute_indicator", json=_INDICATOR_BODY)
    assert response.status_code == 401


# --- negative #3: revoke된 토큰은 즉시 거부(AI-2 즉시성) ---


async def test_revoked_agent_token_is_rejected(client, token_repo: PostgresAgentTokenRepository):
    tenant_id = uuid4()
    issued = await issue(token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.READ}))
    await revoke_token(token_repo, tenant_id=tenant_id, token_id=issued.token.token_id, reason="t")

    response = await client.post(
        "/mcp/tools/compute_indicator",
        json=_INDICATOR_BODY,
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 401


# --- negative #4: 만료된 토큰은 거부 ---


async def test_expired_agent_token_is_rejected(client, token_repo: PostgresAgentTokenRepository):
    issued = await issue(
        token_repo,
        tenant_id=uuid4(),
        scopes=frozenset({Scope.READ}),
        ttl=timedelta(seconds=1),
        now=NOW - timedelta(hours=1),
    )
    response = await client.post(
        "/mcp/tools/compute_indicator",
        json=_INDICATOR_BODY,
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 401


# --- negative #5: 스코프가 없으면 403(AI_SCOPE_DENIED) -- 상승 없음 ---


async def test_wrong_scope_agent_token_is_rejected(
    client, token_repo: PostgresAgentTokenRepository
):
    issued = await issue(token_repo, tenant_id=uuid4(), scopes=frozenset({Scope.PROPOSE}))
    response = await client.post(
        "/mcp/tools/compute_indicator",
        json=_INDICATOR_BODY,
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 403
    assert "AI_SCOPE_DENIED" in response.text


# --- 실패 주입: 위임 대상이 예외를 내면 200으로 위장하지 않고 그대로 전파 ---


async def test_delegate_failure_is_not_swallowed(
    client, token_repo: PostgresAgentTokenRepository, monkeypatch: pytest.MonkeyPatch
):
    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("indicator engine unreachable")

    monkeypatch.setattr(research_tools, "compute_indicator", _boom)
    issued = await issue(token_repo, tenant_id=uuid4(), scopes=frozenset({Scope.READ}))

    response = await client.post(
        "/mcp/tools/compute_indicator",
        json=_INDICATOR_BODY,
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 500  # not 200 -- the failure is not disguised as success


# --- 수치 성능 단언: 인가(HTTP 헤더 검사 + AI-4 DB 왕복) + read 도구 왕복 ---

_MCP_ROUNDTRIP_P95_BUDGET_MS = 200.0
"""AI-4 자체 DB 왕복 상한(50ms, test_token_lifecycle.py)에 ASGI 요청/응답
직렬화와 지표 계산 한 번을 더한 상한 -- 이 leaf 전용 SLO는 spec §7에 없어
그 상한을 기준선으로 넉넉히 잡는다."""


def _p95(samples: list[float]) -> float:
    samples = sorted(samples)
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


async def test_mcp_roundtrip_p95_within_budget(client, token_repo: PostgresAgentTokenRepository):
    issued = await issue(token_repo, tenant_id=uuid4(), scopes=frozenset({Scope.READ}))
    samples: list[float] = []
    for _ in range(20):
        started = time.perf_counter()
        response = await client.post(
            "/mcp/tools/compute_indicator",
            json=_INDICATOR_BODY,
            headers={AGENT_TOKEN_HEADER: issued.secret},
        )
        samples.append((time.perf_counter() - started) * 1000)
        assert response.status_code == 200

    p95_ms = _p95(samples)
    print(f"[AI-15 mcp server] p95={p95_ms:.2f}ms budget<{_MCP_ROUNDTRIP_P95_BUDGET_MS:.0f}ms")
    assert p95_ms < _MCP_ROUNDTRIP_P95_BUDGET_MS


# --- 게이트 적색 재현 ---


async def test_gate_red_budget_actually_fails_past_budget(
    client, token_repo: PostgresAgentTokenRepository
):
    issued = await issue(token_repo, tenant_id=uuid4(), scopes=frozenset({Scope.READ}))
    samples: list[float] = []
    for _ in range(5):
        started = time.perf_counter()
        await client.post(
            "/mcp/tools/compute_indicator",
            json=_INDICATOR_BODY,
            headers={AGENT_TOKEN_HEADER: issued.secret},
        )
        samples.append((time.perf_counter() - started) * 1000)

    absurdly_low_budget_ms = 1e-9
    with pytest.raises(AssertionError):
        assert _p95(samples) < absurdly_low_budget_ms
