"""R-15/R-23 `activate_rule_bundle` 깊이 보강(D3) — task-10541.

`test_rule_bundle_activation.py`가 이미 500줄 경계(CLAUDE.md §7/§6#12)라
여기 별도 파일로 분리한다. `pool`/`repo`/`audit_repo` fixture와 `_draft`/
`_scope` helper는 그 파일과 동일하게 자체 정의한다(이 디렉터리의 다른
test_*.py들도 같은 패턴 — cross-module 픽스처 import는 ruff F811과 충돌).

Spec: docs/specs/L4_risk_and_safety_v1.0.md §9 R-15/R-23.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.risk.policy_bundle import BundleState, RiskRuleBundle
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.risk_gate.adapters.postgres_bundle_repository import (
    PostgresBundleRepository,
)
from src.foundation.risk_gate.application.activate_rule_bundle import (
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


async def test_activate_rule_bundle_propagates_audit_repo_dependency_failure(
    repo, audit_repo, monkeypatch
):
    """실패 주입 — `record_command_event`가 의존하는 `audit_repo.append_event`가
    예외를 던지면(예: 감사 저장소 DB 커넥션 단절), `activate_rule_bundle`은 그
    예외를 삼키지 않고 그대로 전파해야 한다(fail-closed, §3). repo.transition이
    이미 ACTIVE로 커밋된 뒤에 감사 기록이 실패하는 순서이므로, 호출자는 반드시
    예외를 받아 그 사실을 알 수 있어야 한다 — 조용히 성공한 것처럼 보이면 안
    된다."""
    draft = await repo.insert_draft(_draft(scope=_scope(), version="v1"))
    approved = await approve_rule_bundle(
        repo,
        audit_repo,
        bundle_id=draft.id,
        approver_subject_id=uuid4(),
        approval_ref="adr-1",
        actor_is_risk_officer=True,
    )

    async def _broken_append_event(*args, **kwargs):
        raise asyncpg.PostgresConnectionError("injected: audit DB unreachable")

    monkeypatch.setattr(audit_repo, "append_event", _broken_append_event)

    with pytest.raises(asyncpg.PostgresConnectionError, match="injected"):
        await activate_rule_bundle(
            repo,
            audit_repo,
            bundle_id=approved.id,
            actor_subject_id=uuid4(),
            actor_is_risk_officer=True,
        )


async def test_gate_red_dropping_worm_trigger_lets_rule_hash_update_through(repo, pool):
    """게이트 적색 재현 — `test_worm_trigger_blocks_rule_hash_update`(원본
    파일)가 막는 `rule_hash` UPDATE는 실제로 `risk_rule_bundle_worm_guard_trg`
    트리거 때문에 막힌다는 것을 증명한다. 트랜잭션 안에서 그 트리거만
    걷어내면(끝에서 롤백) 같은 UPDATE가 통과해야 한다 — 통과하지 않는다면 그
    WORM 부정테스트는 다른 우연한 장치(예: 컬럼 권한)가 지켰던 것이지 이
    트리거가 지킨 게 아니다."""
    draft = await repo.insert_draft(_draft(scope=_scope(), version="v1"))
    async with pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute("DROP TRIGGER risk_rule_bundle_worm_guard_trg ON risk_rule_bundle")
            await conn.execute(
                "UPDATE risk_rule_bundle SET rule_hash = $2 WHERE id = $1",
                draft.id,
                "e" * 64,
            )
            row = await conn.fetchrow(
                "SELECT rule_hash FROM risk_rule_bundle WHERE id = $1", draft.id
            )
            assert row["rule_hash"] == "e" * 64, (
                "트리거를 걷어냈는데도 rule_hash UPDATE가 막혔다 — WORM 보호는 이"
                "트리거가 아닌 다른 장치가 제공하고 있다는 뜻"
            )
        finally:
            await tr.rollback()

    # 롤백 후 트리거는 복원되어 원래 보호가 여전히 살아있어야 한다.
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.RaiseError, match="WORM violation"):
            async with conn.transaction():
                await conn.execute(
                    "UPDATE risk_rule_bundle SET rule_hash = $2 WHERE id = $1",
                    draft.id,
                    "f" * 64,
                )
