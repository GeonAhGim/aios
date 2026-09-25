"""AI-4 adversarial -- 교차 테넌트 404 (DoD 원문).

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §9 AI-4 DoD
("opaque 저장, 교차 테넌트 404"). connections의 동일 패턴
([[tests/foundation/adversarial/connections/test_cross_tenant_isolation.py]])
을 그대로 따른다: 존재하지 않음과 다른 tenant 소유는 애플리케이션
계층에서 별개 예외로 구분되지만 둘 다 404로 수렴하고, 리포지토리 공개
메서드를 애플리케이션 계층 우회로 직접(공격자 tenant_id로) 호출해도
독립적으로 막힌다는 것까지 증명한다.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.ai.gateway.adapters.postgres_token_repository import (
    PostgresAgentTokenRepository,
)
from src.foundation.ai.gateway.application.errors import (
    AgentTokenNotFoundError,
    CrossTenantAgentTokenAccessError,
)
from src.foundation.ai.gateway.application.issue_token import issue_token
from src.foundation.ai.gateway.application.revoke_token import revoke_token
from src.foundation.ai.gateway.domain.token_rules import Scope

_NOW = datetime.now(timezone.utc)


@pytest.fixture
async def pool():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
def repo(pool: asyncpg.Pool) -> PostgresAgentTokenRepository:
    return PostgresAgentTokenRepository(pool)


async def _owned_token(repo: PostgresAgentTokenRepository, tenant_id):
    issued = await issue_token(
        repo,
        tenant_id=tenant_id,
        scopes=frozenset({Scope.READ}),
        allow_instruments=frozenset(),
        notional_cap=Decimal("0"),
        ttl=timedelta(hours=1),
        now=_NOW,
    )
    return issued.token


async def test_cannot_revoke_another_tenants_token(repo: PostgresAgentTokenRepository):
    owner_id = uuid4()
    attacker_id = uuid4()
    owned = await _owned_token(repo, owner_id)

    with pytest.raises(CrossTenantAgentTokenAccessError):
        await revoke_token(repo, tenant_id=attacker_id, token_id=owned.token_id, reason="attack")

    still_active = await repo.get_token(owned.token_id)
    assert still_active is not None
    assert still_active.revoked_at is None


async def test_nonexistent_token_raises_not_found_not_cross_tenant(
    repo: PostgresAgentTokenRepository,
):
    with pytest.raises(AgentTokenNotFoundError):
        await revoke_token(repo, tenant_id=uuid4(), token_id=uuid4(), reason="ghost")


async def test_revoke_token_rejects_mismatched_tenant_at_repo_layer(
    repo: PostgresAgentTokenRepository,
):
    """게이트 적색 재현: 애플리케이션 계층의 `get_token` 선검사를 건너뛰고
    리포지토리 공개 메서드를 공격자의 tenant_id로 직접 호출해도 -- WHERE
    tenant_id가 없었다면(적색) 아래 raw UPDATE처럼 조용히 성공했을 것을 --
    실제 리포지토리(녹색)는 `(token_id, tenant_id)` 조합 자체를 찾지 못해
    `None`을 돌려주고, 대상 행은 건드리지 않는다."""
    owner_id = uuid4()
    attacker_id = uuid4()
    owned = await _owned_token(repo, owner_id)

    result = await repo.revoke_token(owned.token_id, tenant_id=attacker_id, reason="bypass")
    assert result is None  # 방어선이 있으므로(녹색) 대상 행을 찾지 못했다

    still_active = await repo.get_token(owned.token_id)
    assert still_active is not None
    assert still_active.revoked_at is None

    # 적색 재현: WHERE tenant_id 조건이 없는 raw UPDATE라면 공격자가
    # 성공했을 것임을 같은 행에 대해 직접 증명한다 -- 이 assertion이
    # 실패하면(=raw UPDATE도 막힌다면) 위 pytest.raises가 아무 것도
    # 증명하지 못하는 tautology라는 뜻이다.
    async with repo._pool.acquire() as conn:  # noqa: SLF001 -- 게이트 적색 재현 전용
        raw_result = await conn.execute(
            "UPDATE agent_token SET revoked_at = now(), revoke_reason = 'bypass-raw' "
            "WHERE token_id = $1 AND revoked_at IS NULL",
            owned.token_id,
        )
    assert raw_result == "UPDATE 1"

    truly_revoked = await repo.get_token(owned.token_id)
    assert truly_revoked is not None
    assert truly_revoked.revoked_at is not None
