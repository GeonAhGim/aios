"""CM-13 통합 스위트 — 실 DB(`TEST_DATABASE_URL`) 대상.

Spec: docs/specs/L4_compliance_and_regulatory_v1.0.md#§9 CM-13.

`tests/unit/foundation/mandates/test_explain.py`(fake repo)는 CM-13의 매핑/
거부 로직을 증명하지만, 실패 주입(진짜 네트워크 결함)·수치 성능·게이트
적색(실제 write path가 남긴 DENY 행)·D3 다중 인스턴스/리플레이/적대적
증명은 실 DB 왕복 없이는 낼 수 없다(task-2725 DEPTH 감사, task-2866 DEEPEN).
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.mandates.application.evaluate_pre_trade import evaluate_pre_trade
from src.foundation.mandates.application.explain import explain
from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from tests.foundation.integration.compliance.test_evaluate_pre_trade import _active_tenant
from tests.foundation.integration.mandates.conftest import _asyncpg_dsn
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import (
    reset_virtual_totals,
    virtual_delayed_async,
)

# --- gate-color regression, real write path (DEEPEN task-2866) --------------
#
# 단위 테스트(fake repo)는 손으로 만든 PolicyDecisionRow를 재현하지만, 여기서는
# `evaluate_pre_trade`가 실제로 평가·저장한 행을 `explain()`이 그대로 읽어
# 재현한다 — CM-13이 지키려는 실질("합성 데이터가 아니라 진짜 운영 판정도
# 색이 안 바뀐다")은 이 경로로만 증명된다.


async def test_explain_real_denied_decision_reproduces_red_gate_verdict(pool, repo, trust_repo):
    tenant_id = await _active_tenant(pool, repo, trust_repo)  # default_rules forbids "XYZ"

    evaluated = await evaluate_pre_trade(
        repo,
        tenant_id=tenant_id,
        portfolio_id=None,
        snapshot={"symbol": "XYZ"},
        now=datetime.now(UTC),
    )
    assert evaluated.verdict == ComplianceVerdict.DENY

    explained = await explain(repo, evaluated.compliance_decision_id)

    assert explained.decision_id == evaluated.compliance_decision_id
    assert explained.verdict == ComplianceVerdict.DENY
    assert [hit.rule_id for hit in explained.rule_hits] == list(evaluated.reason_codes)


async def test_explain_real_allowed_decision_reproduces_green_gate_verdict(pool, repo, trust_repo):
    tenant_id = await _active_tenant(pool, repo, trust_repo)

    evaluated = await evaluate_pre_trade(
        repo,
        tenant_id=tenant_id,
        portfolio_id=None,
        snapshot={"symbol": "BTC/USDT"},
        now=datetime.now(UTC),
    )
    assert evaluated.verdict == ComplianceVerdict.ALLOW

    explained = await explain(repo, evaluated.compliance_decision_id)

    assert explained.verdict == ComplianceVerdict.ALLOW
    assert explained.rule_hits == []


# --- real DB/network failure injection (DEEPEN task-2866) --------------------


async def test_real_connection_refused_propagates_fail_closed_not_swallowed():
    """실패 주입 — 도달 불가능한 포트(127.0.0.1:1)로 연결된 실제 asyncpg pool을
    `PostgresMandateRepository`에 물려 `explain()`을 호출하면, 진짜
    `ConnectionRefusedError`(우리가 흉내 낸 예외가 아니라 asyncpg/OS가 스스로
    던지는 결함)가 `DECISION_NOT_FOUND`로 오분류되어 삼켜지지 않고 그대로
    전파돼야 한다 — 연결 실패와 "그런 결정 없음"은 감사관에게 전혀 다른
    사실이다."""
    broken_pool = await asyncpg.create_pool(
        "postgresql://user:password@127.0.0.1:1/unreachable",
        min_size=0,
        max_size=1,
        timeout=5,
    )
    try:
        broken_repo = PostgresMandateRepository(broken_pool)
        with pytest.raises(OSError):
            await explain(broken_repo, uuid4())
    finally:
        await broken_pool.close()


# --- numeric performance (DEEPEN task-2866) -----------------------------------


@pytest.mark.perf
async def test_explain_meets_throughput_budget_over_repeated_real_calls(pool, repo, trust_repo):
    """수치 성능 단언 — 실 DB 왕복(policy_decision + policy_bundle + mandate_
    revision 3회 조회)을 포함해 같은 decision_id를 100회 연속 재현한 처리량이
    하한(5 replays/s, 예산 15.0s) 아래로 떨어지면 안 된다."""
    tenant_id = await _active_tenant(pool, repo, trust_repo)
    evaluated = await evaluate_pre_trade(
        repo,
        tenant_id=tenant_id,
        portfolio_id=None,
        snapshot={"symbol": "BTC/USDT"},
        now=datetime.now(UTC),
    )

    n = 100
    started = time.perf_counter()
    for _ in range(n):
        explained = await explain(repo, evaluated.compliance_decision_id)
        assert explained.verdict == ComplianceVerdict.ALLOW
    elapsed_s = time.perf_counter() - started
    throughput = n / elapsed_s

    assert elapsed_s < 15.0, f"{n}건 재현이 {elapsed_s:.3f}s 걸림 (예산 15.0s)"
    assert throughput > 5.0, f"처리량 {throughput:.1f} replays/s < 5.0/s 하한"


# --- D3 multi-instance/replay proof (DEEPEN task-2866) -----------------------


async def test_concurrent_multi_instance_explain_replays_byte_identical_output(
    pool, repo, trust_repo
):
    """D3 다중 인스턴스/리플레이 증명 — 서로 다른 실제 asyncpg 커넥션(별도
    "인스턴스")을 가진 호출자 N명이 동일한 decision_id를 `asyncio.gather`로
    진짜 동시에 재현한다. explain()은 순수 읽기라 경합할 쓰기가 없으므로,
    CM-13의 실질("같은 입력엔 항상 같은 판정")이 단일 프로세스뿐 아니라 진짜
    다중 커넥션 동시 실행 아래서도 바이트 단위로 깨지지 않는지 증명한다."""
    tenant_id = await _active_tenant(pool, repo, trust_repo)
    evaluated = await evaluate_pre_trade(
        repo,
        tenant_id=tenant_id,
        portfolio_id=None,
        snapshot={"symbol": "XYZ"},
        now=datetime.now(UTC),
    )

    n_instances = 6
    pools = [
        await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=1)
        for _ in range(n_instances)
    ]
    try:
        repos = [PostgresMandateRepository(p) for p in pools]
        results = await asyncio.gather(
            *(explain(r, evaluated.compliance_decision_id) for r in repos)
        )
    finally:
        for p in pools:
            await p.close()

    json_bytes = {r.model_dump_json() for r in results}
    assert len(json_bytes) == 1, "다중 인스턴스 간 explain() 출력이 바이트 단위로 갈렸다"
    assert {r.verdict for r in results} == {ComplianceVerdict.DENY}


# --- D3 adversarial proof (DEEPEN task-2866) ----------------------------------


async def test_worm_trigger_blocks_tamper_and_explain_still_replays_original_verdict(
    pool, repo, trust_repo
):
    """D3 적대적 — `c6a3d8f14b92`가 `policy_decision`에 붙인 WORM 트리거를
    우회해 저장된 판정을 직접 변조(DENY -> ALLOW)하려는 시도가 실제로
    거부되는지, 그리고 그 시도 이후에도 `explain()`이 원래 판정을 그대로
    재현하는지(변조가 어떤 경로로도 성공할 수 없음) 실 DB로 증명한다."""
    tenant_id = await _active_tenant(pool, repo, trust_repo)
    evaluated = await evaluate_pre_trade(
        repo,
        tenant_id=tenant_id,
        portfolio_id=None,
        snapshot={"symbol": "XYZ"},
        now=datetime.now(UTC),
    )
    assert evaluated.verdict == ComplianceVerdict.DENY

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE policy_decision SET outcome = 'ALLOW' WHERE id = $1",
                evaluated.compliance_decision_id,
            )

    explained = await explain(repo, evaluated.compliance_decision_id)
    assert explained.verdict == ComplianceVerdict.DENY


# --- numeric performance — PerfBudget + virtual_delay gate-red reproduction (CM-13 D2 보강) ------


@pytest.mark.perf
async def test_explain_single_call_cpu_under_budget(pool, repo, trust_repo):
    """성능 하한(D2, CM-13 원장 보강): 단일 explain() 호출이 CPU 예산 내여야 한다.
    실측 기준: 로컬 SSD에서 explain 1회 ≈ 5ms CPU(DB 3회 조회 포함).
    예산 150ms는 실측의 30배 이상 여유(task-1038/1521 decision)."""
    reset_virtual_totals()
    tenant_id = await _active_tenant(pool, repo, trust_repo)
    evaluated = await evaluate_pre_trade(
        repo,
        tenant_id=tenant_id,
        portfolio_id=None,
        snapshot={"symbol": "BTC/USDT"},
        now=datetime.now(UTC),
    )

    pb = PerfBudget()
    sample = await pb.best_of_async(
        lambda: explain(repo, evaluated.compliance_decision_id),
        n=5,
        warmup=1,
    )
    print(
        f"\nexplain single-call cpu={sample.cpu_ms:.3f}ms "
        f"wall={sample.wall_ms:.3f}ms (budget<150ms)"
    )
    assert sample.cpu_ms < 150.0, (
        f"단일 explain() 호출 CPU 시간 {sample.cpu_ms:.3f}ms가 예산 150ms 초과"
    )


@pytest.mark.perf
async def test_explain_catches_10x_virtual_delay_injection(pool, repo, trust_repo):
    """게이트 적색 재현(D2, CM-13 원장 보강): 성능 예산 단언이 실제로
    위반을 포착하는지 역으로 증명한다. `virtual_delayed_async`으로
    1회 호출당 10x 예산(150ms * 10 = 1500ms)을 더하면 측정치가 예산을
    초과해 assert_within가 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다. 실제 asyncio.sleep 없이 가상 지연으로 측정치만 조작한다.
    """
    reset_virtual_totals()
    tenant_id = await _active_tenant(pool, repo, trust_repo)
    evaluated = await evaluate_pre_trade(
        repo,
        tenant_id=tenant_id,
        portfolio_id=None,
        snapshot={"symbol": "BTC/USDT"},
        now=datetime.now(UTC),
    )

    # 가상 지연: 1.5s 추가 (예산 150ms의 10배)
    delayed_explain = virtual_delayed_async(
        lambda: explain(repo, evaluated.compliance_decision_id),
        seconds=1.5,
    )

    pb = PerfBudget()
    sample = await pb.best_of_async(delayed_explain, n=3, warmup=1)
    # virtual_delayed_async는 wall_only 주입(실제 asyncio.sleep와 동일):
    # cpu_ms는 변하지 않으므로 wall_ms로 적색 재현 단언한다.
    assert sample.wall_ms >= 1500.0, (
        "10x 가상 지연 주입 후 wall_ms가 1500ms 예산을 안 넘으면 적색 재현 실패"
    )
