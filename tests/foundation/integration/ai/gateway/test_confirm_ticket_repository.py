"""AI-16 integration test -- `postgres_confirm_ticket_repository.py`.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.1 AI-4
(`ConfirmTicket`), §9 AI-16 DoD ("confirmation token round trip +
adversarial: ticket reuse -> 409"). ADR-2026-09-09-C D2: negative >= 3,
failure injection 1, numeric perf assertion 1, red-gate reproduction 1.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.ai.gateway.adapters.postgres_confirm_ticket_repository import (
    PostgresConfirmTicketRepository,
)
from src.foundation.ai.gateway.adapters.postgres_token_repository import (
    PostgresAgentTokenRepository,
)
from src.foundation.ai.gateway.application.issue_token import issue_token
from src.foundation.ai.gateway.domain.token_rules import Scope

_NOW = datetime.now(timezone.utc)
_DIGEST = "a" * 64


@pytest.fixture
async def pool():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = await asyncpg.create_pool(dsn, min_size=2, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def repo(pool: asyncpg.Pool) -> PostgresConfirmTicketRepository:
    return PostgresConfirmTicketRepository(pool)


@pytest.fixture
def token_repo(pool: asyncpg.Pool) -> PostgresAgentTokenRepository:
    return PostgresAgentTokenRepository(pool)


async def _issued_token_id(token_repo: PostgresAgentTokenRepository) -> tuple[object, object]:
    """Returns `(tenant_id, token_id)` for a freshly-issued token -- every
    `confirm_ticket` row FK-references `agent_token(token_id)`."""
    tenant_id = uuid4()
    issued = await issue_token(
        token_repo,
        tenant_id=tenant_id,
        scopes=frozenset({Scope.PAPER}),
        allow_instruments=frozenset(),
        notional_cap=Decimal("0"),
        ttl=timedelta(hours=1),
        now=_NOW,
    )
    return tenant_id, issued.token.token_id


# --- 정상 경로: issue -> get -> mark_consumed round trip ---


async def test_issue_get_mark_consumed_round_trip(
    repo: PostgresConfirmTicketRepository, token_repo: PostgresAgentTokenRepository
):
    tenant_id, token_id = await _issued_token_id(token_repo)
    ticket = await repo.issue(
        tenant_id=tenant_id,
        created_by_token=token_id,
        action_digest=_DIGEST,
        expires_at=_NOW + timedelta(minutes=5),
    )
    assert ticket.consumed_at is None

    fetched = await repo.get(ticket.ticket_id)
    assert fetched is not None
    assert fetched.action_digest == _DIGEST
    assert fetched.consumed_at is None

    consumed = await repo.mark_consumed(
        ticket.ticket_id, execute_digest=_DIGEST, now=datetime.now(timezone.utc)
    )
    assert consumed is not None
    assert consumed.consumed_at is not None

    # already consumed -- WHERE consumed_at IS NULL matches zero rows
    replay = await repo.mark_consumed(
        ticket.ticket_id, execute_digest=_DIGEST, now=datetime.now(timezone.utc)
    )
    assert replay is None


# --- negative #1: 존재하지 않는 ticket_id는 None(예외 아님) ---


async def test_get_unknown_ticket_returns_none(repo: PostgresConfirmTicketRepository):
    assert await repo.get(uuid4()) is None


# --- negative #2: mark_consumed도 존재하지 않는 ticket_id는 None ---


async def test_mark_consumed_unknown_ticket_returns_none(repo: PostgresConfirmTicketRepository):
    result = await repo.mark_consumed(uuid4(), execute_digest=_DIGEST, now=_NOW)
    assert result is None


# --- negative #3: digest가 어긋나면 마킹되지 않는다(두 번째 방어선, WHERE에 핀) ---


async def test_mark_consumed_rejects_digest_mismatch(
    repo: PostgresConfirmTicketRepository, token_repo: PostgresAgentTokenRepository
):
    tenant_id, token_id = await _issued_token_id(token_repo)
    ticket = await repo.issue(
        tenant_id=tenant_id,
        created_by_token=token_id,
        action_digest=_DIGEST,
        expires_at=_NOW + timedelta(minutes=5),
    )
    result = await repo.mark_consumed(ticket.ticket_id, execute_digest="b" * 64, now=_NOW)
    assert result is None

    still_open = await repo.get(ticket.ticket_id)
    assert still_open is not None
    assert still_open.consumed_at is None


# --- 실패 주입: 동시(race) 소비 요청 중 정확히 하나만 성공한다 ---


async def test_concurrent_mark_consumed_only_one_request_wins(
    repo: PostgresConfirmTicketRepository, token_repo: PostgresAgentTokenRepository
):
    """`confirm_promotion`이 두 번 거의 동시에 도착해도(재시도/이중 클릭)
    한쪽만 성공하고 다른 쪽은 재사용으로 거부돼야 한다(spec §9 AI-16 DoD
    "적대적 재사용 -> 409"의 동시성 축)."""
    tenant_id, token_id = await _issued_token_id(token_repo)
    ticket = await repo.issue(
        tenant_id=tenant_id,
        created_by_token=token_id,
        action_digest=_DIGEST,
        expires_at=_NOW + timedelta(minutes=5),
    )

    results = await asyncio.gather(
        repo.mark_consumed(
            ticket.ticket_id, execute_digest=_DIGEST, now=datetime.now(timezone.utc)
        ),
        repo.mark_consumed(
            ticket.ticket_id, execute_digest=_DIGEST, now=datetime.now(timezone.utc)
        ),
    )
    winners = [r for r in results if r is not None]
    assert len(winners) == 1


# --- 게이트 적색 재현: `consumed_at IS NULL` 가드를 뺀 UPDATE라면 둘 다 성공했을 것 ---


async def test_without_the_guard_clause_both_concurrent_updates_would_win(
    pool: asyncpg.Pool, token_repo: PostgresAgentTokenRepository
):
    """위 실패 주입 테스트가 tautology가 아님을 증명한다 -- 같은 행에
    `AND consumed_at IS NULL` 가드 없이 두 개의 동시 UPDATE를 직접 실행하면
    (real adapter가 절대 만들지 않는 SQL 모양) 둘 다 매치해 두 번째 UPDATE도
    영향을 받는다는 것을 보여, 실제 어댑터의 가드절이 그 결과를 실제로
    막고 있다는 인과를 확인한다."""
    tenant_id, token_id = await _issued_token_id(token_repo)
    async with pool.acquire() as conn:
        ticket_row = await conn.fetchrow(
            "INSERT INTO confirm_ticket (tenant_id, created_by_token, action_digest, expires_at) "
            "VALUES ($1, $2, $3, $4) RETURNING ticket_id",
            tenant_id,
            token_id,
            _DIGEST,
            _NOW + timedelta(minutes=5),
        )
    ticket_id = ticket_row["ticket_id"]

    async def _unguarded_update() -> asyncpg.Record | None:
        async with pool.acquire() as conn:
            return await conn.fetchrow(
                "UPDATE confirm_ticket SET consumed_at = now() "
                "WHERE ticket_id = $1 RETURNING ticket_id",
                ticket_id,
            )

    results = await asyncio.gather(_unguarded_update(), _unguarded_update())
    # 적색: 가드 없이는 두 UPDATE 모두 행을 매치한다(재사용을 막지 못했을 것).
    assert all(r is not None for r in results)


# --- 수치 성능 단언: get() DB 왕복(confirm_promotion마다 최소 1회 호출) ---

_GET_DB_ROUNDTRIP_P95_BUDGET_MS = 50.0


def _p95(samples: list[float]) -> float:
    samples = sorted(samples)
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


async def test_get_db_roundtrip_p95_within_budget(
    repo: PostgresConfirmTicketRepository, token_repo: PostgresAgentTokenRepository
):
    tenant_id, token_id = await _issued_token_id(token_repo)
    ticket = await repo.issue(
        tenant_id=tenant_id,
        created_by_token=token_id,
        action_digest=_DIGEST,
        expires_at=_NOW + timedelta(minutes=5),
    )

    samples: list[float] = []
    for _ in range(30):
        started = time.perf_counter()
        await repo.get(ticket.ticket_id)
        samples.append((time.perf_counter() - started) * 1000)

    p95_ms = _p95(samples)
    print(
        f"[AI-16 confirm_ticket] get() db roundtrip p95={p95_ms:.2f}ms "
        f"budget<{_GET_DB_ROUNDTRIP_P95_BUDGET_MS:.1f}ms"
    )
    assert p95_ms < _GET_DB_ROUNDTRIP_P95_BUDGET_MS
