"""L4_compliance_and_regulatory_v1.0.md#9 CM-4 — `policy_bundle`/
`policy_decision` append-only(WORM) enforcement, 실제 dev DB 대상.

CM-A4 "판정 레코드는 append-only" 증명: `c6a3d8f14b92`가 두 테이블에 붙인
`worm_sql()` 트리거는 owner 역할에도 예외 없이 발동하므로, 이 테스트는
애플리케이션이 쓰는 것과 같은 커넥션으로 직접 UPDATE/DELETE를 시도해
실제로 막히는지 확인한다(REVOKE만으로는 테이블 소유자를 막지 못한다 —
`src/core/db/append_only.py` 모듈 docstring)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.mandates.application.activate_revision import (
    MATERIAL_CHANGE_CONSENT_PURPOSE,
)
from src.foundation.mandates.application.activate_revision import (
    activate_revision as activate_revision_command,
)
from src.foundation.mandates.application.create_draft_mandate import create_draft_mandate
from src.foundation.mandates.application.evaluate_policy import evaluate as evaluate_policy_command
from src.foundation.mandates.application.propose_amendment import propose_amendment
from src.foundation.mandates.contracts.v1 import PolicyEvaluationSubject
from src.foundation.mandates.domain.models import PolicyBundle
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.foundation.trust.application.accept_disclosure import accept_disclosure
from src.foundation.trust.contracts.v1 import TenantContext as TrustTenantContext
from tests.foundation.integration.mandates.conftest import backdate_cooling_off, default_rules
from tests.foundation.integration.trust.conftest import create_disclosure
from tests.integration.conftest import create_test_tenant


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresMandateRepository(pool)


@pytest.fixture
def trust_repo(pool):
    return PostgresTrustRepository(pool)


async def _activated_tenant(pool, repo, trust_repo, **rule_overrides):
    tenant_id = await create_test_tenant(pool)
    draft = await create_draft_mandate(
        repo, tenant_id=tenant_id, subject_id=tenant_id, rules=default_rules(**rule_overrides)
    )
    await activate_revision_command(
        repo,
        trust_repo,
        tenant_id=tenant_id,
        subject_id=tenant_id,
        revision_id=draft.id,
        reauthenticated=False,
    )
    return tenant_id


async def _consent_to_material_change(pool, trust_repo, tenant_id) -> None:
    """material change 게이트를 통과시키기 위한 최신 동의 — `get_active_disclosure()`
    (FND-01)는 purpose당 revision이 가장 큰 것을 "최신"으로 취급하므로,
    다른 통합테스트 파일(test_mandate_lifecycle.py)이 같은
    MATERIAL_CHANGE_CONSENT_PURPOSE로 이미 남겨둔 행보다 반드시 더 큰
    revision을 써야 한다 — 현재 MAX(revision)+1을 조회해서 쓴다."""
    async with pool.acquire() as conn:
        current_max = await conn.fetchval(
            "SELECT COALESCE(MAX(revision), 0) FROM disclosure WHERE purpose = $1",
            MATERIAL_CHANGE_CONSENT_PURPOSE,
        )
    revision = current_max + 1
    await create_disclosure(pool, purpose=MATERIAL_CHANGE_CONSENT_PURPOSE, revision=revision)
    trust_context = TrustTenantContext(tenant_id=tenant_id, subject_id=tenant_id, mfa_verified=True)
    await accept_disclosure(
        trust_repo,
        trust_context,
        purpose=MATERIAL_CHANGE_CONSENT_PURPOSE,
        disclosure_revision=revision,
    )


async def _activate_material_amendment(pool, repo, trust_repo, tenant_id):
    """95% 노출 한도로 개정 → material change 3중 게이트(재인증·최신 동의·
    cooling-off)를 모두 통과시켜 activate까지 진행한다(test_mandate_lifecycle.py
    `test_material_amendment_after_all_gates_pass_activates_and_supersedes`와
    동일 절차)."""
    await _consent_to_material_change(pool, trust_repo, tenant_id)
    proposed = await propose_amendment(
        repo, tenant_id=tenant_id, rules=default_rules(max_total_exposure_pct=95.0)
    )
    await backdate_cooling_off(pool, proposed.id, seconds_ago=120)
    await activate_revision_command(
        repo,
        trust_repo,
        tenant_id=tenant_id,
        subject_id=tenant_id,
        revision_id=proposed.id,
        reauthenticated=True,
    )
    return proposed


async def test_policy_decision_update_is_rejected_by_worm_trigger(pool, repo, trust_repo):
    """DoD (1) — 이미 기록된 policy_decision 행 1건에 UPDATE를 시도하면
    WORM 트리거가 예외를 던진다."""
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    decision = await evaluate_policy_command(
        repo, tenant_id=tenant_id, subject=PolicyEvaluationSubject(command_type="x")
    )

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE policy_decision SET outcome = 'DENY' WHERE id = $1", decision.id
            )


async def test_policy_decision_delete_is_rejected_by_worm_trigger(pool, repo, trust_repo):
    """DoD (1)의 짝 — DELETE도 같은 트리거로 막혀야 append-only가 성립한다."""
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    decision = await evaluate_policy_command(
        repo, tenant_id=tenant_id, subject=PolicyEvaluationSubject(command_type="x")
    )

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM policy_decision WHERE id = $1", decision.id)


async def test_policy_bundle_update_is_rejected_by_worm_trigger(pool, repo, trust_repo):
    """policy_bundle도 같은 `worm_sql()` 트리거를 쓴다 — rule_hash를 사후에
    바꿔치기하는 시도가 막히는지 직접 확인한다."""
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    await evaluate_policy_command(
        repo, tenant_id=tenant_id, subject=PolicyEvaluationSubject(command_type="x")
    )
    mandate = await repo.get_mandate(tenant_id)
    bundle = await repo.get_bundle_for_revision(mandate.active_revision_id)
    assert bundle is not None

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE policy_bundle SET rule_hash = 'forged' WHERE id = $1", bundle.id
            )


async def test_bundle_activation_history_keeps_prior_row_untouched(pool, repo, trust_repo):
    """DoD (2) — 활성 revision이 A에서 B로 바뀌어도(mandate가 새 policy_bundle을
    참조하게 되어도) A의 policy_bundle 행은 UPDATE되지 않고 그대로 남고, B는
    별도의 새 행으로 추가된다(append-only 번들 이력)."""
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    mandate = await repo.get_mandate(tenant_id)
    revision_a_id = mandate.active_revision_id

    await evaluate_policy_command(
        repo, tenant_id=tenant_id, subject=PolicyEvaluationSubject(command_type="x")
    )
    bundle_a_before = await repo.get_bundle_for_revision(revision_a_id)
    assert bundle_a_before is not None

    proposed = await _activate_material_amendment(pool, repo, trust_repo, tenant_id)

    await evaluate_policy_command(
        repo, tenant_id=tenant_id, subject=PolicyEvaluationSubject(command_type="x")
    )
    bundle_b = await repo.get_bundle_for_revision(proposed.id)
    assert bundle_b is not None
    assert bundle_b.id != bundle_a_before.id

    bundle_a_after = await repo.get_bundle_for_revision(revision_a_id)
    assert bundle_a_after == bundle_a_before  # 이전 행이 UPDATE되지 않고 그대로 남았다


async def test_insert_policy_bundle_race_returns_existing_row_without_update(
    pool, repo, trust_repo
):
    """`insert_policy_bundle()`은 이제 `ON CONFLICT ... DO NOTHING` + 재조회로
    바뀌었다(policy_bundle이 전체 행 WORM이라 자기 자신에 대한 no-op UPDATE도
    금지) — 같은 revision에 대해 두 번 호출해도 두 번째 호출이 예외 없이
    첫 번째와 동일한 행을 돌려줘야 한다."""
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    mandate = await repo.get_mandate(tenant_id)
    revision = await repo.get_revision(mandate.active_revision_id)
    assert revision is not None

    first = await repo.insert_policy_bundle(
        PolicyBundle(
            id=uuid4(),
            mandate_revision_id=revision.id,
            compiler_version="v1",
            rule_hash="a" * 64,
            created_at=datetime.now(timezone.utc),
        )
    )
    second = await repo.insert_policy_bundle(
        PolicyBundle(
            id=uuid4(),
            mandate_revision_id=revision.id,
            compiler_version="v2-should-be-ignored",
            rule_hash="b" * 64,
            created_at=datetime.now(timezone.utc),
        )
    )

    assert second.id == first.id
    assert second.rule_hash == first.rule_hash  # 두 번째 호출의 값으로 UPDATE되지 않았다
