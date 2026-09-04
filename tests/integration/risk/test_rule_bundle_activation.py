"""R-22/R-23 `risk_rule_bundle` 통합테스트 — partial unique·WORM 컬럼·
conditional 전이(R-22) + 승인/활성화 커맨드(R-23). 실 DB(`TEST_DATABASE_URL`)
대상.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §9 R-22/R-23.
DoD(R-22): scope당 ACTIVE 2개는 partial unique로 거부(동시 activate 경합으로
실증), rule_hash/policy_snapshot WORM(소유자 우회 포함), transition()의
expected_state 불일치는 실패로 드러남, 교차 scope 조회는 0건.
DoD(R-23): 승인자=작성자 거부, approval_ref 필수, 감사 이벤트.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.risk.policy_bundle import BundleState, RiskRuleBundle
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.risk_gate.adapters.postgres_bundle_repository import (
    PostgresBundleRepository,
)
from src.foundation.risk_gate.application.activate_rule_bundle import (
    MissingApprovalRefError,
    RuleBundleNotFoundError,
    SelfApprovalError,
    UnauthorizedRuleBundleActorError,
    activate_rule_bundle,
    approve_rule_bundle,
)


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[3] / ".env")
    url = env.get("DATABASE_URL")
    assert url, ".env에 DATABASE_URL이 없습니다"
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresBundleRepository(pool)


@pytest.fixture
def audit_repo(pool):
    return PostgresAuditEventRepository(pool)


def _scope(prefix: str = "S") -> str:
    """`scope`는 `VARCHAR(30)`이라 uuid4().hex(32자) 전체를 붙이면 넘친다."""
    return f"{prefix}-{uuid4().hex[:20]}"


def _draft(*, scope: str, version: str, rule_hash: str | None = None) -> RiskRuleBundle:
    return RiskRuleBundle(
        id=uuid4(),
        scope=scope,
        version=version,
        rule_hash=rule_hash or ("a" * 64),
        engine_version="engine-v1",
        policy_snapshot={"version": version},
        state=BundleState.DRAFT,
        created_by=uuid4(),
    )


async def _approve(conn: asyncpg.Connection, bundle_id) -> None:
    """`APPROVED`까지는 전이 규칙 검증 대상이 아니므로(그건 R-23) 테스트
    준비 단계에서 직접 UPDATE로 만들어 둔다."""
    await conn.execute(
        "UPDATE risk_rule_bundle SET state = 'APPROVED', approved_by = $2, "
        "approval_ref = 'test-approval' WHERE id = $1",
        bundle_id,
        uuid4(),
    )


async def test_get_active_returns_none_when_no_bundle(repo):
    assert await repo.get_active(_scope()) is None


async def test_insert_draft_then_get_active_after_transition(repo, pool):
    scope = _scope()
    draft = await repo.insert_draft(_draft(scope=scope, version="v1"))
    assert draft.state == BundleState.DRAFT

    async with pool.acquire() as conn:
        await _approve(conn, draft.id)

    activated = await repo.transition(
        draft.id, expected_state=BundleState.APPROVED, new_state=BundleState.ACTIVE
    )
    assert activated.state == BundleState.ACTIVE

    active = await repo.get_active(scope)
    assert active is not None
    assert active.id == draft.id


async def test_transition_with_wrong_expected_state_raises_not_silently_zero_rows(repo):
    scope = _scope()
    draft = await repo.insert_draft(_draft(scope=scope, version="v1"))

    with pytest.raises(ConcurrencyConflictError):
        await repo.transition(
            draft.id, expected_state=BundleState.ACTIVE, new_state=BundleState.RETIRED
        )

    # 실패한 전이는 실제로 아무것도 바꾸지 않았어야 한다.
    unchanged = await repo.get_active(scope)
    assert unchanged is None


async def test_cross_scope_get_active_returns_zero(repo, pool):
    scope_a = _scope("A")
    scope_b = _scope("B")
    draft = await repo.insert_draft(_draft(scope=scope_a, version="v1"))
    async with pool.acquire() as conn:
        await _approve(conn, draft.id)
    await repo.transition(
        draft.id, expected_state=BundleState.APPROVED, new_state=BundleState.ACTIVE
    )

    assert await repo.get_active(scope_a) is not None
    assert await repo.get_active(scope_b) is None


async def test_concurrent_activate_of_two_bundles_same_scope_only_one_succeeds(repo, pool):
    """I6 — partial unique `ux_bundle_active`가 scope당 ACTIVE 1개를 DB에서
    강제한다는 것을, 동시 activate 2트랜잭션 경합으로 실증한다."""
    scope = _scope()
    bundle_a = await repo.insert_draft(_draft(scope=scope, version="v1"))
    bundle_b = await repo.insert_draft(_draft(scope=scope, version="v2"))
    async with pool.acquire() as conn:
        await _approve(conn, bundle_a.id)
        await _approve(conn, bundle_b.id)

    async def activate(bundle_id):
        return await repo.transition(
            bundle_id, expected_state=BundleState.APPROVED, new_state=BundleState.ACTIVE
        )

    results = await asyncio.gather(
        activate(bundle_a.id), activate(bundle_b.id), return_exceptions=True
    )
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], asyncpg.UniqueViolationError)


async def test_worm_trigger_blocks_rule_hash_update(repo, pool):
    draft = await repo.insert_draft(_draft(scope=_scope(), version="v1"))
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.RaiseError, match="WORM violation"):
            async with conn.transaction():
                await conn.execute(
                    "UPDATE risk_rule_bundle SET rule_hash = $2 WHERE id = $1",
                    draft.id,
                    "b" * 64,
                )


async def test_worm_trigger_blocks_policy_snapshot_update(repo, pool):
    draft = await repo.insert_draft(_draft(scope=_scope(), version="v1"))
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.RaiseError, match="WORM violation"):
            async with conn.transaction():
                await conn.execute(
                    "UPDATE risk_rule_bundle SET policy_snapshot = '{}'::jsonb WHERE id = $1",
                    draft.id,
                )


async def test_worm_trigger_blocks_table_owner_too(pool):
    """REVOKE는 테이블 소유자를 막지 못한다 — `pool`은 마이그레이션을 실행한
    소유자 계정으로 접속하므로(SET ROLE 없음), 여기서 막히면 REVOKE가 아니라
    트리거 자체가 우회 불가하다는 뜻이다."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO risk_rule_bundle "
            "(id, scope, version, rule_hash, engine_version, policy_snapshot, state, created_by) "
            "VALUES ($1, $2, $3, $4, $5, $6::jsonb, 'DRAFT', $7) RETURNING id",
            uuid4(),
            _scope(),
            "v1",
            "c" * 64,
            "engine-v1",
            "{}",
            uuid4(),
        )
        bundle_id = row["id"]

        with pytest.raises(asyncpg.RaiseError, match="WORM violation"):
            async with conn.transaction():
                await conn.execute(
                    "UPDATE risk_rule_bundle SET rule_hash = $2 WHERE id = $1",
                    bundle_id,
                    "d" * 64,
                )


async def test_worm_trigger_allows_state_transition_unrelated_columns(repo, pool):
    """WORM은 rule_hash/policy_snapshot/version만 잠근다 — state 등 다른
    컬럼의 정상 전이는 여전히 가능해야 한다(과잉 차단 방지 회귀)."""
    draft = await repo.insert_draft(_draft(scope=_scope(), version="v1"))
    async with pool.acquire() as conn:
        await _approve(conn, draft.id)
    activated = await repo.transition(
        draft.id, expected_state=BundleState.APPROVED, new_state=BundleState.ACTIVE
    )
    assert activated.state == BundleState.ACTIVE
    assert activated.rule_hash == draft.rule_hash


# --- R-23 approve_rule_bundle / activate_rule_bundle ---


async def _count_audit_rows(pool, aggregate_id: UUID) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS n FROM foundation_audit_event WHERE aggregate_id = $1",
            aggregate_id,
        )
    return int(row["n"])


async def test_approve_rule_bundle_rejects_non_risk_officer(repo, audit_repo):
    draft = await repo.insert_draft(_draft(scope=_scope(), version="v1"))
    with pytest.raises(UnauthorizedRuleBundleActorError):
        await approve_rule_bundle(
            repo,
            audit_repo,
            bundle_id=draft.id,
            approver_subject_id=uuid4(),
            approval_ref="adr-1",
            actor_is_risk_officer=False,
        )
    assert await _count_audit_rows(repo._pool, draft.id) == 0
    unchanged = await repo.get_by_id(draft.id)
    assert unchanged.state == BundleState.DRAFT


async def test_approve_rule_bundle_rejects_missing_approval_ref(repo, audit_repo):
    draft = await repo.insert_draft(_draft(scope=_scope(), version="v1"))
    with pytest.raises(MissingApprovalRefError):
        await approve_rule_bundle(
            repo,
            audit_repo,
            bundle_id=draft.id,
            approver_subject_id=uuid4(),
            approval_ref="",
            actor_is_risk_officer=True,
        )
    assert await _count_audit_rows(repo._pool, draft.id) == 0


async def test_approve_rule_bundle_rejects_self_approval(repo, audit_repo):
    """4-eyes 원칙 — 작성자 본인은 자기 번들을 승인할 수 없다."""
    draft = await repo.insert_draft(_draft(scope=_scope(), version="v1"))
    with pytest.raises(SelfApprovalError):
        await approve_rule_bundle(
            repo,
            audit_repo,
            bundle_id=draft.id,
            approver_subject_id=draft.created_by,
            approval_ref="adr-1",
            actor_is_risk_officer=True,
        )
    unchanged = await repo.get_by_id(draft.id)
    assert unchanged.state == BundleState.DRAFT
    assert await _count_audit_rows(repo._pool, draft.id) == 0


async def test_approve_rule_bundle_raises_not_found(repo, audit_repo):
    with pytest.raises(RuleBundleNotFoundError):
        await approve_rule_bundle(
            repo,
            audit_repo,
            bundle_id=uuid4(),
            approver_subject_id=uuid4(),
            approval_ref="adr-1",
            actor_is_risk_officer=True,
        )


async def test_approve_rule_bundle_success_transitions_and_audits_once(repo, audit_repo):
    draft = await repo.insert_draft(_draft(scope=_scope(), version="v1"))
    approver = uuid4()
    approved = await approve_rule_bundle(
        repo,
        audit_repo,
        bundle_id=draft.id,
        approver_subject_id=approver,
        approval_ref="adr-42",
        actor_is_risk_officer=True,
    )
    assert approved.state == BundleState.APPROVED
    assert approved.approved_by == approver
    assert approved.approval_ref == "adr-42"
    assert await _count_audit_rows(repo._pool, draft.id) == 1


async def test_approve_rule_bundle_wrong_expected_state_raises_concurrency_conflict(
    repo, audit_repo
):
    draft = await repo.insert_draft(_draft(scope=_scope(), version="v1"))
    await approve_rule_bundle(
        repo,
        audit_repo,
        bundle_id=draft.id,
        approver_subject_id=uuid4(),
        approval_ref="adr-1",
        actor_is_risk_officer=True,
    )
    # 이미 APPROVED — 다시 approve하면 expected_state=DRAFT 불일치로 거부된다.
    with pytest.raises(ConcurrencyConflictError):
        await approve_rule_bundle(
            repo,
            audit_repo,
            bundle_id=draft.id,
            approver_subject_id=uuid4(),
            approval_ref="adr-2",
            actor_is_risk_officer=True,
        )


async def test_activate_rule_bundle_rejects_non_risk_officer(repo, audit_repo):
    draft = await repo.insert_draft(_draft(scope=_scope(), version="v1"))
    approved = await approve_rule_bundle(
        repo,
        audit_repo,
        bundle_id=draft.id,
        approver_subject_id=uuid4(),
        approval_ref="adr-1",
        actor_is_risk_officer=True,
    )
    with pytest.raises(UnauthorizedRuleBundleActorError):
        await activate_rule_bundle(
            repo,
            audit_repo,
            bundle_id=approved.id,
            actor_subject_id=uuid4(),
            actor_is_risk_officer=False,
        )
    unchanged = await repo.get_by_id(approved.id)
    assert unchanged.state == BundleState.APPROVED


async def test_activate_rule_bundle_success_sets_active_and_audits(repo, audit_repo):
    scope = _scope()
    draft = await repo.insert_draft(_draft(scope=scope, version="v1"))
    approved = await approve_rule_bundle(
        repo,
        audit_repo,
        bundle_id=draft.id,
        approver_subject_id=uuid4(),
        approval_ref="adr-1",
        actor_is_risk_officer=True,
    )
    activated = await activate_rule_bundle(
        repo,
        audit_repo,
        bundle_id=approved.id,
        actor_subject_id=uuid4(),
        actor_is_risk_officer=True,
    )
    assert activated.state == BundleState.ACTIVE
    assert activated.activated_at is not None
    assert activated.effective_from is not None
    # approve 1건 + activate 1건.
    assert await _count_audit_rows(repo._pool, draft.id) == 2
    assert (await repo.get_active(scope)).id == draft.id


async def test_activate_rule_bundle_from_draft_raises_concurrency_conflict(repo, audit_repo):
    draft = await repo.insert_draft(_draft(scope=_scope(), version="v1"))
    with pytest.raises(ConcurrencyConflictError):
        await activate_rule_bundle(
            repo,
            audit_repo,
            bundle_id=draft.id,
            actor_subject_id=uuid4(),
            actor_is_risk_officer=True,
        )
