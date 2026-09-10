"""CM-8 통합 스위트 — 실 DB(`TEST_DATABASE_URL`) 대상.

Spec: docs/specs/L4_compliance_and_regulatory_v1.0.md#§9 CM-8, §3, §6.

`evaluate_pre_trade`가 CM-6/CM-7 규칙 번들을 실제로 평가하고 CM-4
`policy_decision` 테이블에 판정을 남기는지, 그리고 실패 모드(mandate
없음/번들 비활성)가 문서화된 예외로 fail-closed되는지 증명한다.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
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
from tests.foundation.integration.mandates.conftest import _asyncpg_dsn, default_rules
from tests.integration.conftest import create_test_tenant


async def _active_tenant(pool, repo, trust_repo, **rule_overrides):
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


async def test_no_mandate_raises_missing_error(pool, repo):
    tenant_id = await create_test_tenant(pool)

    with pytest.raises(ComplianceMandateMissingError):
        await evaluate_pre_trade(
            repo,
            tenant_id=tenant_id,
            portfolio_id=None,
            snapshot={"symbol": "BTC/USDT"},
            now=datetime.now(timezone.utc),
        )


async def test_paused_revision_raises_bundle_inactive(pool, repo, trust_repo):
    tenant_id = await _active_tenant(pool, repo, trust_repo)
    await pause_mandate(repo, tenant_id=tenant_id)

    with pytest.raises(ComplianceBundleInactiveError):
        await evaluate_pre_trade(
            repo,
            tenant_id=tenant_id,
            portfolio_id=None,
            snapshot={"symbol": "BTC/USDT"},
            now=datetime.now(timezone.utc),
        )


async def test_restricted_symbol_denies(pool, repo, trust_repo):
    tenant_id = await _active_tenant(pool, repo, trust_repo)  # default_rules forbids "XYZ"

    result = await evaluate_pre_trade(
        repo,
        tenant_id=tenant_id,
        portfolio_id=None,
        snapshot={"symbol": "XYZ"},
        now=datetime.now(timezone.utc),
    )

    assert result.verdict == ComplianceVerdict.DENY
    assert "restricted_list" in result.reason_codes
    stored = await repo.get_policy_decision(result.compliance_decision_id)
    assert stored is not None
    assert stored.command_type == "PRE_TRADE_COMPLIANCE"


async def test_allowed_symbol_allows_and_persists(pool, repo, trust_repo):
    tenant_id = await _active_tenant(pool, repo, trust_repo)

    result = await evaluate_pre_trade(
        repo,
        tenant_id=tenant_id,
        portfolio_id=None,
        snapshot={"symbol": "BTC/USDT"},
        now=datetime.now(timezone.utc),
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
        repo,
        tenant_id=tenant_id,
        portfolio_id=None,
        snapshot={},
        now=datetime.now(timezone.utc),
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
        repo,
        tenant_id=tenant_id,
        portfolio_id=None,
        snapshot={"symbol": "BTC/USDT"},
        now=now,
    )
    second = await evaluate_pre_trade(
        repo,
        tenant_id=tenant_id,
        portfolio_id=None,
        snapshot={"symbol": "BTC/USDT"},
        now=now,
    )

    assert second.compliance_decision_id == first.compliance_decision_id


async def test_different_tenants_get_independent_decisions(pool, repo, trust_repo):
    tenant_a = await _active_tenant(pool, repo, trust_repo)
    tenant_b = await _active_tenant(pool, repo, trust_repo)
    now = datetime.now(timezone.utc)

    result_a = await evaluate_pre_trade(
        repo,
        tenant_id=tenant_a,
        portfolio_id=None,
        snapshot={"symbol": "XYZ"},
        now=now,
    )
    result_b = await evaluate_pre_trade(
        repo,
        tenant_id=tenant_b,
        portfolio_id=None,
        snapshot={"symbol": "BTC/USDT"},
        now=now,
    )

    assert result_a.verdict == ComplianceVerdict.DENY
    assert result_b.verdict == ComplianceVerdict.ALLOW
    assert result_a.compliance_decision_id != result_b.compliance_decision_id


async def test_unrelated_random_tenant_still_raises_mandate_missing(pool, repo):
    """negative — 존재하지 않는 임의 tenant_id는 mandate가 없다는 이유로
    거부되지, 어떤 다른 tenant의 mandate와도 섞이지 않는다."""
    with pytest.raises(ComplianceMandateMissingError):
        await evaluate_pre_trade(
            repo,
            tenant_id=uuid4(),
            portfolio_id=None,
            snapshot={"symbol": "BTC/USDT"},
            now=datetime.now(timezone.utc),
        )


# --- Real DB/network failure injection (DEEPEN task-2861) -------------------
#
# task-2725 DEPTH 감사(docs/audit/DEPTH_CM.md)가 지적한 대로, `tests/
# adversarial/compliance/test_no_bypass.py`의 기존 monkeypatch(`evaluate_
# compliance_gate`를 always-ALLOW 스텁으로 치환)는 배선 증명이지 DB/네트워크
# 결함 주입이 아니다. 아래 테스트는 실제로 존재하지 않는 포트에 연결을 시도해
# asyncpg가 스스로 던지는 진짜 `ConnectionRefusedError`(OS 레벨 네트워크 결함,
# 우리가 흉내 낸 예외 타입이 아니다)를 `PostgresMandateRepository`에 흘려보내,
# `evaluate_pre_trade`가 그 결함을 삼켜 조용히 ALLOW로 새지 않고 그대로
# 전파함(fail-closed)을 증명한다.


async def test_real_connection_refused_propagates_fail_closed_not_swallowed():
    """실패 주입 — 도달 불가능한 포트(127.0.0.1:1)로 연결된 실제 asyncpg pool을
    `PostgresMandateRepository`에 연결해 `evaluate_pre_trade`를 호출하면, 그
    누구도 그 실패를 삼켜 "mandate 없음/ALLOW"로 오분류하지 않고 진짜
    `ConnectionRefusedError`가 호출자까지 그대로 전파되어야 한다."""
    broken_pool = await asyncpg.create_pool(
        "postgresql://user:password@127.0.0.1:1/unreachable",
        min_size=0,
        max_size=1,
        timeout=5,
    )
    try:
        broken_repo = PostgresMandateRepository(broken_pool)
        with pytest.raises(OSError):
            await evaluate_pre_trade(
                broken_repo,
                tenant_id=uuid4(),
                portfolio_id=None,
                snapshot={"symbol": "BTC/USDT"},
                now=datetime.now(timezone.utc),
            )
    finally:
        await broken_pool.close()


# --- Numeric performance/latency (DEEPEN task-2861) --------------------------


async def test_evaluate_pre_trade_meets_latency_budget(pool, repo, trust_repo):
    """수치 성능/지연 단언: 실 DB 왕복(mandate/revision 조회 + 번들 캐시
    조회 + policy_decision INSERT)을 포함해 서로 다른 심볼 100건을 연속
    평가한 처리량이 하한(5 evaluations/s, 예산 15.0s) 아래로 떨어지면 안
    된다 — 지금까지는 성공/거부 여부만 확인했을 뿐 수치 상한이 없었다."""
    tenant_id = await _active_tenant(pool, repo, trust_repo)
    now = datetime.now(timezone.utc)

    n = 100
    started = time.perf_counter()
    for i in range(n):
        result = await evaluate_pre_trade(
            repo,
            tenant_id=tenant_id,
            portfolio_id=None,
            snapshot={"symbol": f"PERF-{i}/USDT"},
            now=now,
        )
        assert result.verdict == ComplianceVerdict.ALLOW
    elapsed_s = time.perf_counter() - started
    throughput = n / elapsed_s

    assert elapsed_s < 15.0, f"{n}건 평가가 {elapsed_s:.3f}s 걸림 (예산 15.0s)"
    assert throughput > 5.0, f"처리량 {throughput:.1f} evaluations/s < 5.0/s 하한"


# --- D3 multi-instance/replay proof (DEEPEN task-2861) -----------------------


async def test_concurrent_multi_instance_evaluation_replays_identical_verdict(
    pool, repo, trust_repo
):
    """D3 다중 인스턴스/리플레이 증명 — 서로 다른 실제 asyncpg 커넥션(별도
    "인스턴스"를 흉내)을 가진 호출자 N명이 정확히 동일한 (tenant, snapshot)
    입력으로 `evaluate_pre_trade`를 `asyncio.gather`로 진짜 동시에 호출한다.
    이 리프의 캐시 조회(`get_cached_decision`)는 자기 자신의 INSERT가
    커밋되기 전까지는 아직 아무것도 찾지 못하므로, N명 전원이 실제로 겹치는
    평가·쓰기 경합 아래서 판정을 내린다. `policy_decision`에는 CM-4의
    `policy_bundle`과 달리 UNIQUE(tenant_id, command_fingerprint) 제약이 없어
    (인덱스만 있음) 이 경합이 항상 단일 행으로 수렴한다는 보장은 없다 — 그래서
    이 테스트는 행 개수 수렴을 단언하지 않는다. 대신 CM-13 "설명 재현"이
    요구하는 실질(같은 입력 -> 같은 판정)이 단일 프로세스가 아니라 진짜
    다중 커넥션 동시 실행 아래서도 깨지지 않는지 증명한다: N명 전원의
    verdict/reason_codes가 완전히 동일해야 하고, DB에 남은 그 fingerprint의
    모든 policy_decision 행(1개든 여러 개든)의 outcome/reason_codes 내용도
    서로 다른 인스턴스 사이에서 오염 없이 전부 동일해야 한다."""
    tenant_id = await _active_tenant(pool, repo, trust_repo)
    now = datetime.now(timezone.utc)
    snapshot = {"symbol": "XYZ"}  # default_rules가 forbidden_assets로 지정 -> DENY

    n_instances = 6
    pools = [
        await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=1)
        for _ in range(n_instances)
    ]
    try:
        repos = [PostgresMandateRepository(p) for p in pools]

        async def _attempt(instance_repo: PostgresMandateRepository):
            return await evaluate_pre_trade(
                instance_repo,
                tenant_id=tenant_id,
                portfolio_id=None,
                snapshot=snapshot,
                now=now,
            )

        results = await asyncio.gather(*(_attempt(r) for r in repos))
    finally:
        for p in pools:
            await p.close()

    verdicts = {r.verdict for r in results}
    reason_code_sets = {r.reason_codes for r in results}
    assert verdicts == {ComplianceVerdict.DENY}, "다중 인스턴스 판정이 서로 달랐다(비결정적 재현)"
    assert reason_code_sets == {("restricted_list",)}, "reason_codes가 인스턴스 사이에서 갈렸다"

    fingerprints = {r.compliance_decision_id for r in results}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT outcome, reason_codes FROM policy_decision WHERE id = ANY($1::uuid[])",
            list(fingerprints),
        )
    assert len(rows) >= 1
    outcomes = {row["outcome"] for row in rows}
    reason_rows = {tuple(row["reason_codes"]) for row in rows}
    assert outcomes == {"DENY"}, "DB에 남은 판정 행의 outcome이 인스턴스 사이에서 갈렸다"
    assert reason_rows == {("restricted_list",)}, "DB에 남은 판정 행의 reason_codes가 갈렸다"
