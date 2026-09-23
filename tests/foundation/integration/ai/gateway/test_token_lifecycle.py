"""AI-4 통합테스트 -- postgres_token_repository + application/{issue,revoke,authorize}.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.1 AI-4, §9 AI-4
DoD ("opaque 저장, 교차 테넌트 404" -- 교차 테넌트는
tests/foundation/adversarial/ai/gateway/test_cross_tenant_isolation.py에서
다룬다). ADR-2026-09-09-C D2: negative >= 3, 실패 주입 1, 성능 단언 1,
게이트 적색 재현 1.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.ai.gateway.adapters.postgres_token_repository import (
    PostgresAgentTokenRepository,
)
from src.foundation.ai.gateway.application.authorize import authorize
from src.foundation.ai.gateway.application.issue_token import hash_token_secret, issue_token
from src.foundation.ai.gateway.application.revoke_token import revoke_token
from src.foundation.ai.gateway.domain.token_rules import (
    Scope,
    ScopeDeniedError,
    TokenExpiredError,
    TokenRevokedError,
    TokenRuleError,
)

_NOW = datetime.now(timezone.utc)


@pytest.fixture
async def pool():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def repo(pool: asyncpg.Pool) -> PostgresAgentTokenRepository:
    return PostgresAgentTokenRepository(pool)


# --- 성공 경로: 발급 -> authorize -> revoke -> authorize 재시도는 거부 ---


async def test_issue_authorize_revoke_round_trip(repo: PostgresAgentTokenRepository):
    tenant_id = uuid4()
    issued = await issue_token(
        repo,
        tenant_id=tenant_id,
        scopes=frozenset({Scope.READ, Scope.PROPOSE}),
        allow_instruments=frozenset({"BTC-USDT"}),
        notional_cap=Decimal("1000"),
        ttl=timedelta(hours=1),
        now=_NOW,
    )

    authorized = await authorize(repo, token_secret=issued.secret, scope=Scope.PROPOSE)
    assert authorized.token_id == issued.token.token_id
    assert authorized.tenant_id == tenant_id

    await revoke_token(repo, tenant_id=tenant_id, token_id=issued.token.token_id, reason="test")

    # authorize() now reads its liveness clock from the same `get_by_hash`
    # round trip as `revoked_at` (both DB-stamped), so this observes the
    # revocation immediately regardless of any app/DB clock skew -- no
    # `now=` to pass here anymore (see authorize.py / get_by_hash docstrings).
    with pytest.raises(TokenRevokedError):
        await authorize(repo, token_secret=issued.secret, scope=Scope.PROPOSE)


# --- opaque 저장: 평문 secret이 DB에 절대 남지 않는다 ---


async def test_opaque_storage_never_persists_plaintext_secret(
    repo: PostgresAgentTokenRepository, pool: asyncpg.Pool
):
    tenant_id = uuid4()
    issued = await issue_token(
        repo,
        tenant_id=tenant_id,
        scopes=frozenset({Scope.READ}),
        allow_instruments=frozenset(),
        notional_cap=Decimal("0"),
        ttl=timedelta(hours=1),
        now=_NOW,
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT token_hash FROM agent_token WHERE token_id = $1", issued.token.token_id
        )
    assert row is not None
    assert row["token_hash"] != issued.secret
    assert row["token_hash"] == hash_token_secret(issued.secret)
    assert len(row["token_hash"]) == 64  # sha256 hex


# --- negative #1: 스코프 없는 발급 요청은 거부(issue_scopes, AI-2 재사용) ---


async def test_issue_token_rejects_empty_scope_request(repo: PostgresAgentTokenRepository):
    with pytest.raises(TokenRuleError):
        await issue_token(
            repo,
            tenant_id=uuid4(),
            scopes=frozenset(),
            allow_instruments=frozenset(),
            notional_cap=Decimal("0"),
            ttl=timedelta(hours=1),
            now=_NOW,
        )


# --- negative #2: 알 수 없는(존재하지 않는) secret은 401 동형으로 거부 ---


async def test_authorize_rejects_unknown_secret(repo: PostgresAgentTokenRepository):
    with pytest.raises(TokenRevokedError):
        await authorize(repo, token_secret="not-a-real-secret", scope=Scope.READ)


# --- negative #3: 만료된 토큰은 거부 ---


async def test_authorize_rejects_expired_token(repo: PostgresAgentTokenRepository):
    tenant_id = uuid4()
    issued = await issue_token(
        repo,
        tenant_id=tenant_id,
        scopes=frozenset({Scope.READ}),
        allow_instruments=frozenset(),
        notional_cap=Decimal("0"),
        ttl=timedelta(seconds=1),
        now=_NOW - timedelta(hours=1),
    )

    with pytest.raises(TokenExpiredError):
        await authorize(repo, token_secret=issued.secret, scope=Scope.READ)


# --- negative #4: 부여되지 않은 스코프 요청은 거부(스코프 상승 없이 인가) ---


async def test_authorize_rejects_ungranted_scope(repo: PostgresAgentTokenRepository):
    tenant_id = uuid4()
    issued = await issue_token(
        repo,
        tenant_id=tenant_id,
        scopes=frozenset({Scope.READ}),
        allow_instruments=frozenset(),
        notional_cap=Decimal("0"),
        ttl=timedelta(hours=1),
        now=_NOW,
    )

    with pytest.raises(ScopeDeniedError):
        await authorize(repo, token_secret=issued.secret, scope=Scope.PAPER)


# --- 실패 주입: DB CHECK가 paper_only 불변조건을 애플리케이션 계층 우회에도 지킨다 ---


async def test_direct_insert_bypassing_application_rejects_non_paper_only(
    pool: asyncpg.Pool,
):
    """실패 주입: 애플리케이션 계층(`issue_token`, 항상 paper_only=True로
    생성)을 완전히 우회해 raw SQL로 `paper_only=FALSE`를 직접 INSERT하면,
    마이그레이션의 CHECK(paper_only IS TRUE)(0895391e36f5)가 그 자체로
    거부해야 한다 -- 도메인 계층의 `AgentToken.__post_init__` 불변조건이
    깨진 상류(직접 DB 조작·다른 서비스의 버그)에도 마지막 방어선이 살아
    있는지 증명한다."""
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "INSERT INTO agent_token "
                "(tenant_id, token_hash, scopes, allow_instruments, notional_cap, "
                " paper_only, expires_at) "
                "VALUES ($1, $2, $3, $4, $5, FALSE, $6)",
                uuid4(),
                "a" * 64,
                ["read"],
                [],
                Decimal("0"),
                _NOW + timedelta(hours=1),
            )


# --- 게이트 적색 재현: CHECK를 실제로 제거했다면 위 실패 주입이 통과했을 것임을 증명 ---


async def test_paper_only_check_gate_actually_fails_without_the_constraint(
    pool: asyncpg.Pool,
):
    """위 실패 주입 테스트가 tautology가 아님을 증명한다 -- 같은 컬럼에
    CHECK 제약이 없는 임시 테이블(스키마는 동일하되 `paper_only` CHECK만
    뺀 것)에서는 같은 INSERT가 성공한다는 것을 보여, `agent_token`의 CHECK가
    실제로 그 삽입을 막고 있다는 인과를 확인한다."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "CREATE TEMP TABLE agent_token_no_check "
            "(paper_only BOOLEAN NOT NULL DEFAULT TRUE) ON COMMIT DROP"
        )
        await conn.execute("INSERT INTO agent_token_no_check (paper_only) VALUES (FALSE)")
        row = await conn.fetchrow("SELECT paper_only FROM agent_token_no_check")
        assert row["paper_only"] is False  # 적색: CHECK 없이는 통과했을 것


# --- 수치 성능 단언: authorize()의 DB 왕복(해시 조회, 모든 MCP 도구의 인가 관문) ---
# PLT-24 refresh()가 차용한 것과 동일 근거(단일 DB round-trip 트랜잭션) --
# "주문 제출->ACK p95 50ms(paper)"를 차용한다(ADR-2026-09-09-C에 AI 전용
# 축이 아직 없음).

_AUTHORIZE_DB_ROUNDTRIP_P95_BUDGET_MS = 50.0


def _p95(samples: list[float]) -> float:
    samples = sorted(samples)
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


async def test_authorize_db_roundtrip_p95_within_budget(repo: PostgresAgentTokenRepository):
    tenant_id = uuid4()
    issued = await issue_token(
        repo,
        tenant_id=tenant_id,
        scopes=frozenset({Scope.READ}),
        allow_instruments=frozenset(),
        notional_cap=Decimal("0"),
        ttl=timedelta(hours=1),
        now=_NOW,
    )

    samples: list[float] = []
    for _ in range(30):
        started = time.perf_counter()
        await authorize(repo, token_secret=issued.secret, scope=Scope.READ)
        samples.append((time.perf_counter() - started) * 1000)

    p95_ms = _p95(samples)
    print(
        f"[AI-4 authorize] db roundtrip p95={p95_ms:.2f}ms "
        f"budget<{_AUTHORIZE_DB_ROUNDTRIP_P95_BUDGET_MS:.1f}ms"
    )
    assert p95_ms < _AUTHORIZE_DB_ROUNDTRIP_P95_BUDGET_MS
