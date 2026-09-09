"""CM-8 통합 스위트 — 실 DB(`TEST_DATABASE_URL`) 대상.

Spec: docs/specs/L4_compliance_and_regulatory_v1.0.md#§9 CM-8, §3, §6.

`evaluate_pre_trade`가 CM-6/CM-7 규칙 번들을 실제로 평가하고 CM-4
`policy_decision` 테이블에 판정을 남기는지, 그리고 실패 모드(mandate
없음/번들 비활성)가 문서화된 예외로 fail-closed되는지 증명한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.foundation.mandates.application.activate_revision import (
    activate_revision as activate_revision_command,
)
from src.foundation.mandates.application.create_draft_mandate import create_draft_mandate
from src.foundation.mandates.application.evaluate_pre_trade import (
    ComplianceBundleInactiveError,
    ComplianceMandateMissingError,
    evaluate_pre_trade,
)
from src.foundation.mandates.application.pause_mandate import pause_mandate
from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from tests.foundation.integration.mandates.conftest import default_rules
from tests.integration.conftest import create_test_tenant


async def _active_tenant(pool, repo, trust_repo, **rule_overrides):
    tenant_id = await create_test_tenant(pool)
    draft = await create_draft_mandate(
        repo, tenant_id=tenant_id, subject_id=tenant_id, rules=default_rules(**rule_overrides)
    )
    await activate_revision_command(
        repo, trust_repo, tenant_id=tenant_id, subject_id=tenant_id,
        revision_id=draft.id, reauthenticated=False,
    )
    return tenant_id


async def test_no_mandate_raises_missing_error(pool, repo):
    tenant_id = await create_test_tenant(pool)

    with pytest.raises(ComplianceMandateMissingError):
        await evaluate_pre_trade(
            repo, tenant_id=tenant_id, portfolio_id=None,
            snapshot={"symbol": "BTC/USDT"}, now=datetime.now(timezone.utc),
        )


async def test_paused_revision_raises_bundle_inactive(pool, repo, trust_repo):
    tenant_id = await _active_tenant(pool, repo, trust_repo)
    await pause_mandate(repo, tenant_id=tenant_id)

    with pytest.raises(ComplianceBundleInactiveError):
        await evaluate_pre_trade(
            repo, tenant_id=tenant_id, portfolio_id=None,
            snapshot={"symbol": "BTC/USDT"}, now=datetime.now(timezone.utc),
        )


async def test_restricted_symbol_denies(pool, repo, trust_repo):
    tenant_id = await _active_tenant(pool, repo, trust_repo)  # default_rules forbids "XYZ"

    result = await evaluate_pre_trade(
        repo, tenant_id=tenant_id, portfolio_id=None,
        snapshot={"symbol": "XYZ"}, now=datetime.now(timezone.utc),
    )

    assert result.verdict == ComplianceVerdict.DENY
    assert "restricted_list" in result.reason_codes
    stored = await repo.get_policy_decision(result.compliance_decision_id)
    assert stored is not None
    assert stored.command_type == "PRE_TRADE_COMPLIANCE"


async def test_allowed_symbol_allows_and_persists(pool, repo, trust_repo):
    tenant_id = await _active_tenant(pool, repo, trust_repo)

    result = await evaluate_pre_trade(
        repo, tenant_id=tenant_id, portfolio_id=None,
        snapshot={"symbol": "BTC/USDT"}, now=datetime.now(timezone.utc),
    )

    assert result.verdict == ComplianceVerdict.ALLOW
    assert result.reason_codes == ()
    stored = await repo.get_policy_decision(result.compliance_decision_id)
    assert stored is not None
    assert stored.tenant_id == tenant_id


async def test_no_symbol_snapshot_still_allows_with_real_decision(pool, repo, trust_repo):
    """execution-start 형태 호출(주문 단위 필드 없음) — 빈 번들이라도 실제
    mandate가 ACTIVE면 진짜 WORM 판정을 남긴다(마커 id가 아니다)."""
    tenant_id = await _active_tenant(pool, repo, trust_repo)

    result = await evaluate_pre_trade(
        repo, tenant_id=tenant_id, portfolio_id=None,
        snapshot={}, now=datetime.now(timezone.utc),
    )

    assert result.verdict == ComplianceVerdict.ALLOW
    stored = await repo.get_policy_decision(result.compliance_decision_id)
    assert stored is not None


async def test_repeated_identical_evaluation_reuses_cached_decision(pool, repo, trust_repo):
    """§5 idempotency — same (bundle_version, inputs) 재평가는 같은
    `policy_decision` 행을 재사용한다(새 행을 또 만들지 않는다)."""
    tenant_id = await _active_tenant(pool, repo, trust_repo)
    now = datetime.now(timezone.utc)

    first = await evaluate_pre_trade(
        repo, tenant_id=tenant_id, portfolio_id=None,
        snapshot={"symbol": "BTC/USDT"}, now=now,
    )
    second = await evaluate_pre_trade(
        repo, tenant_id=tenant_id, portfolio_id=None,
        snapshot={"symbol": "BTC/USDT"}, now=now,
    )

    assert second.compliance_decision_id == first.compliance_decision_id


async def test_different_tenants_get_independent_decisions(pool, repo, trust_repo):
    tenant_a = await _active_tenant(pool, repo, trust_repo)
    tenant_b = await _active_tenant(pool, repo, trust_repo)
    now = datetime.now(timezone.utc)

    result_a = await evaluate_pre_trade(
        repo, tenant_id=tenant_a, portfolio_id=None,
        snapshot={"symbol": "XYZ"}, now=now,
    )
    result_b = await evaluate_pre_trade(
        repo, tenant_id=tenant_b, portfolio_id=None,
        snapshot={"symbol": "BTC/USDT"}, now=now,
    )

    assert result_a.verdict == ComplianceVerdict.DENY
    assert result_b.verdict == ComplianceVerdict.ALLOW
    assert result_a.compliance_decision_id != result_b.compliance_decision_id


async def test_unrelated_random_tenant_still_raises_mandate_missing(pool, repo):
    """negative — 존재하지 않는 임의 tenant_id는 mandate가 없다는 이유로
    거부되지, 어떤 다른 tenant의 mandate와도 섞이지 않는다."""
    with pytest.raises(ComplianceMandateMissingError):
        await evaluate_pre_trade(
            repo, tenant_id=uuid4(), portfolio_id=None,
            snapshot={"symbol": "BTC/USDT"}, now=datetime.now(timezone.utc),
        )
