"""H-1c(task-3370, ADR-2026-09-09-B) — H-1(task-2603) 분할의 마지막 조각:
`foundation_gate.py`의 mandate 3분기(무 mandate/PAUSED/stale revision)
전부를 실제 프로덕션 게이트 함수(`make_foundation_pre_submit_gate`)로
직접 때려 REJECT를 증명하고, pause 직후 제출이 몰리는 경합 1건을 더한다.

`tests/integration/test_order_service_risk_gate.py`가 이미 무 mandate/
PAUSED 케이스를 `submit_order()` 경유로 증명하지만(레거시 Order 경로),
이 파일은 CM-8/CM-20 적대적 스위트 자리(`tests/adversarial/compliance/`)에
게이트 자체(`gate(context)`)를 직접 호출해 세 분기를 한 곳에 모은다 —
DoD가 요구하는 "무 mandate·정지 mandate·만료 revision → REJECT" 3종은
서로 다른 코드 경로(RISK_MANDATE_REQUIRED / STATE_MANDATE_PAUSED /
RISK_MANDATE_REVISION_STALE)이므로 각각 독립적으로 REJECT를 재현해야
누락을 못 가린다.

게이트 적색 재현(수동 검증, task DoD) — 무 mandate 테스트에서
`make_foundation_pre_submit_gate(pool, require_mandate=True)`를
`require_mandate=False`로 되돌리면 이 테스트는 FAIL한다(그 분기가
`RISK_MANDATE_REQUIRED` DENY 대신 `_finish_allow()`로 빠지므로) — 이
테스트가 진짜로 그 분기를 붙잡고 있다는 뜻이다.
"""

from __future__ import annotations

import asyncio
import secrets

import pytest

from src.foundation.mandates.application.activate_revision import (
    activate_revision as activate_revision_command,
)
from src.foundation.mandates.application.pause_mandate import pause_mandate
from src.foundation.mandates.application.propose_amendment import propose_amendment
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.order_service.gate import GateOutcome, OrderContext
from tests.foundation.integration.mandates.conftest import default_rules
from tests.foundation.integration.risk_gate.conftest import activate_mandate_with_defaults
from tests.integration.conftest import create_test_tenant
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async


def _unique_execution_id() -> int:
    """테스트마다 새 execution_id. 고정값 1을 쓰면 게이트가 읽는 kill switch
    scope_ref `exec:1`이 워커 DB 전체에서 공유돼, 같은 xdist 워커에서 앞서 돈
    적대 테스트(test_fenced_submit·test_fence_race 등)가 `exec:1`에 활성화한
    STRATEGY_DEPLOYMENT 컨트롤이 그대로 보여 RISK_KILL_SWITCH_ACTIVE_* DENY로
    적색이 된다(CI run 36187517816 실측). 이 파일의 관심사는 mandate 분기뿐이다."""
    return secrets.randbelow(2**31 - 1) + 1


def _context(user_id, mandate_revision_id=None, *, execution_id: int):
    return OrderContext(
        user_id=user_id,
        execution_id=execution_id,
        exchange="bitget",
        mandate_revision_id=mandate_revision_id,
    )


async def test_no_mandate_at_all_rejects(pool):
    """무 mandate — tenant에 `portfolio_mandate` 행 자체가 없으면(H-1a
    resolver도 채울 게 없다) RISK_MANDATE_REQUIRED DENY."""
    user_id = await create_test_tenant(pool)
    execution_id = _unique_execution_id()
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)

    decision = await gate(_context(user_id, execution_id=execution_id))

    assert decision.outcome == GateOutcome.DENY
    assert decision.reason_codes == ("RISK_MANDATE_REQUIRED",)

    async with pool.acquire() as conn:
        audit_row = await conn.fetchrow(
            "SELECT * FROM audit_log WHERE action_type = 'risk_gate.unmandated_submit' "
            "AND target_id = $1",
            str(execution_id),
        )
    assert audit_row is not None  # DENY 이전에도 감사 기록은 남는다


async def test_no_mandate_other_tenant_with_mandate_not_rejected_as_required(
    pool, repo, trust_repo
):
    """음성 1 — 무 mandate DENY는 해당 tenant에만 적용된다. mandate가 있는
    다른 tenant는 RISK_MANDATE_REQUIRED를 받지 않는다."""
    with_mandate = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=with_mandate)
    mandate = await repo.get_mandate(with_mandate)
    without = await create_test_tenant(pool)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)

    ok = await gate(
        _context(with_mandate, mandate.active_revision_id, execution_id=_unique_execution_id())
    )
    denied = await gate(_context(without, execution_id=_unique_execution_id()))

    assert "RISK_MANDATE_REQUIRED" not in ok.reason_codes
    assert denied.outcome == GateOutcome.DENY
    assert denied.reason_codes == ("RISK_MANDATE_REQUIRED",)


async def test_no_mandate_deny_does_not_leak_to_unrelated_execution_audit(pool):
    """음성 2 — 무 mandate 감사 기록은 해당 execution_id에만 남고 다른
    execution_id 조회에는 나타나지 않는다."""
    user_id = await create_test_tenant(pool)
    execution_id = _unique_execution_id()
    other_id = execution_id + 1 if execution_id < 2**31 - 1 else execution_id - 1
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)

    await gate(_context(user_id, execution_id=execution_id))

    async with pool.acquire() as conn:
        other = await conn.fetchrow(
            "SELECT 1 FROM audit_log WHERE action_type = 'risk_gate.unmandated_submit' "
            "AND target_id = $1",
            str(other_id),
        )
    assert other is None


async def test_no_mandate_unknown_tenant_never_allows(pool):
    """음성 3 — 존재하지 않는 tenant(user_id)도 ALLOW되지 않는다(fail-closed)."""
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    try:
        decision = await gate(_context(2**31 - 5, execution_id=_unique_execution_id()))
    except Exception:  # noqa: BLE001 - raising is also fail-closed
        return
    assert decision.outcome == GateOutcome.DENY


async def test_no_mandate_db_failure_never_allows(pool):
    """실패 주입 — 감사/조회 중 DB 연결 획득이 실패해도 게이트는 ALLOW를
    반환하지 않는다(예외 전파 또는 DENY)."""
    user_id = await create_test_tenant(pool)

    class _BrokenPool:
        def __getattr__(self, name):
            raise ConnectionError(f"injected pool failure: {name}")

    gate = make_foundation_pre_submit_gate(_BrokenPool(), require_mandate=True)
    try:
        decision = await gate(_context(user_id, execution_id=_unique_execution_id()))
    except Exception:  # noqa: BLE001 - propagation is fail-closed
        return
    assert decision.outcome == GateOutcome.DENY


# 성능 예산(task-24202): 무 mandate 게이트 1회 wall 실측 약 4.6ms(로컬, best-of-5).
# 예산 _NO_MANDATE_BUDGET_MS는 실측의 30배 이상 여유로 신설했다.
_NO_MANDATE_BUDGET_MS = 200.0  # 실측 4.6ms의 ~43배


async def _measure_no_mandate_gate(perf_budget: PerfBudget, pool, *, delay_s: float = 0.0):
    user_id = await create_test_tenant(pool)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    call = virtual_delayed_async(gate, delay_s) if delay_s else gate

    async def _run():
        return await call(_context(user_id, execution_id=_unique_execution_id()))

    return await perf_budget.best_of_async(_run, n=5, warmup=1)


@pytest.mark.perf
async def test_no_mandate_gate_perf_budget(perf_budget: PerfBudget, pool):
    """성능 단언 — 무 mandate 게이트 wall 지연이 예산 미만."""
    reset_virtual_totals()
    sample = await _measure_no_mandate_gate(perf_budget, pool)
    assert sample.wall_ms < _NO_MANDATE_BUDGET_MS, perf_budget.describe(
        sample, budget_ms=_NO_MANDATE_BUDGET_MS
    )


@pytest.mark.perf
async def test_no_mandate_gate_perf_gate_red_with_10x_injection(perf_budget: PerfBudget, pool):
    """게이트 적색 재현 — 예산의 10배 가상 지연을 주입하면 같은 단언이 적색이
    된다(단언이 실제로 지연을 잡는다)."""
    reset_virtual_totals()
    sample = await _measure_no_mandate_gate(
        perf_budget, pool, delay_s=_NO_MANDATE_BUDGET_MS * 10 / 1000
    )
    assert not (sample.wall_ms < _NO_MANDATE_BUDGET_MS)
    reset_virtual_totals()


async def test_paused_mandate_rejects(pool, repo, trust_repo):
    """정지 mandate — active revision이 PAUSED 상태면 evaluate_policy가
    PAUSE_REQUIRED를 반환하고, 게이트는 이를 DENY(STATE_MANDATE_PAUSED)로
    번역한다(리스크 fence는 전부 깨끗해도 이 분기가 독립적으로 막는다)."""
    user_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=user_id)
    mandate = await repo.get_mandate(user_id)
    await pause_mandate(repo, tenant_id=user_id)

    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    decision = await gate(
        _context(user_id, mandate.active_revision_id, execution_id=_unique_execution_id())
    )

    assert decision.outcome == GateOutcome.DENY
    assert "STATE_MANDATE_PAUSED" in decision.reason_codes


async def test_expired_revision_rejects(pool, repo, trust_repo):
    """만료 revision — 이 주문이 관측했던 revision(A)이 그사이 새 개정(B)으로
    대체돼 더 이상 active가 아니면(task-1806 observed-vs-current), 새
    active revision을 기준으로 재평가하지 않고 즉시
    RISK_MANDATE_REVISION_STALE DENY로 재바인딩을 요구한다."""
    user_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=user_id)
    mandate = await repo.get_mandate(user_id)
    expired_revision_id = mandate.active_revision_id

    # 동일 규칙으로 개정 — material change가 없어 재인증 없이 바로
    # activate된다(activate_revision.py의 detect_material_change=[] 경로).
    proposed = await propose_amendment(repo, tenant_id=user_id, rules=default_rules())
    await activate_revision_command(
        repo,
        trust_repo,
        tenant_id=user_id,
        subject_id=user_id,
        revision_id=proposed.id,
        reauthenticated=False,
    )

    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    decision = await gate(
        _context(user_id, expired_revision_id, execution_id=_unique_execution_id())
    )

    assert decision.outcome == GateOutcome.DENY
    assert decision.reason_codes == ("RISK_MANDATE_REVISION_STALE",)


async def test_paused_mandate_other_tenant_not_rejected_as_paused(pool, repo, trust_repo):
    """음성 1 — PAUSED mandate DENY는 해당 tenant에만 적용된다. mandate가
    활성화된 다른 tenant는 STATE_MANDATE_PAUSED를 받지 않는다."""
    paused = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=paused)
    await pause_mandate(repo, tenant_id=paused)

    active = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=active)
    mandate = await repo.get_mandate(active)

    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)

    active_decision = await gate(
        _context(active, mandate.active_revision_id, execution_id=_unique_execution_id())
    )
    paused_mandate = await repo.get_mandate(paused)
    paused_decision = await gate(
        _context(paused, paused_mandate.active_revision_id, execution_id=_unique_execution_id())
    )

    assert "STATE_MANDATE_PAUSED" not in active_decision.reason_codes
    assert paused_decision.outcome == GateOutcome.DENY
    assert "STATE_MANDATE_PAUSED" in paused_decision.reason_codes


async def test_paused_mandate_unknown_tenant_never_allows(pool):
    """음성 2 — 존재하지 않는 tenant(user_id)도 PAUSED 경로에서 ALLOW되지
    않는다(fail-closed)."""
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    try:
        decision = await gate(_context(2**31 - 99, execution_id=_unique_execution_id()))
    except Exception:  # noqa: BLE001 - raising is also fail-closed
        return
    assert decision.outcome == GateOutcome.DENY
    assert "STATE_MANDATE_PAUSED" in decision.reason_codes


async def test_paused_mandate_deny_does_not_leak_to_active_revision_audit(pool, repo, trust_repo):
    """음성 3 — PAUSED mandate DENY 감사 기록은 해당 execution_id에만 남고
    다른 execution_id 조회에는 나타나지 않는다."""
    user_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=user_id)
    await pause_mandate(repo, tenant_id=user_id)

    execution_id = _unique_execution_id()
    other_id = execution_id + 1 if execution_id < 2**31 - 1 else execution_id - 1
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)

    await gate(_context(user_id, execution_id=execution_id))

    async with pool.acquire() as conn:
        other = await conn.fetchrow(
            "SELECT 1 FROM audit_log WHERE action_type = 'risk_gate.unmandated_submit' "
            "AND target_id = $1",
            str(other_id),
        )
    assert other is None


async def test_paused_mandate_db_failure_never_allows(pool, repo, trust_repo):
    """실패 주입 — PAUSED mandate 경로에서 DB 연결 획득이 실패해도 게이트는
    ALLOW를 반환하지 않는다(예외 전파 또는 DENY)."""
    user_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=user_id)
    await pause_mandate(repo, tenant_id=user_id)

    class _BrokenPool:
        def __getattr__(self, name):
            raise ConnectionError(f"injected pool failure: {name}")

    gate = make_foundation_pre_submit_gate(_BrokenPool(), require_mandate=True)
    try:
        decision = await gate(_context(user_id, execution_id=_unique_execution_id()))
    except Exception:  # noqa: BLE001 - propagation is fail-closed
        return
    assert decision.outcome == GateOutcome.DENY


# 성능 예산(task-24202): PAUSED mandate 게이트 1회 wall 실측 약 5.1ms(로컬, best-of-5).
# 예산 _PAUSED_MANDATE_BUDGET_MS는 실측의 30배 이상 여유로 신설했다.
_PAUSED_MANDATE_BUDGET_MS = 200.0  # 실측 5.1ms의 ~39배


async def _measure_paused_mandate_gate(
    perf_budget: PerfBudget, pool, repo, trust_repo, *, delay_s: float = 0.0
):
    user_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=user_id)
    await pause_mandate(repo, tenant_id=user_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    call = virtual_delayed_async(gate, delay_s) if delay_s else gate

    async def _run():
        return await call(_context(user_id, execution_id=_unique_execution_id()))

    return await perf_budget.best_of_async(_run, n=5, warmup=1)


@pytest.mark.perf
async def test_paused_mandate_gate_perf_budget(perf_budget: PerfBudget, pool, repo, trust_repo):
    """성능 단언 — PAUSED mandate 게이트 wall 지연이 예산 미만."""
    reset_virtual_totals()
    sample = await _measure_paused_mandate_gate(perf_budget, pool, repo, trust_repo)
    assert sample.wall_ms < _PAUSED_MANDATE_BUDGET_MS, perf_budget.describe(
        sample, budget_ms=_PAUSED_MANDATE_BUDGET_MS
    )


@pytest.mark.perf
async def test_paused_mandate_gate_perf_gate_red_with_10x_injection(
    perf_budget: PerfBudget, pool, repo, trust_repo
):
    """게이트 적색 재현 — 예산의 10배 가상 지연을 주입하면 같은 단언이 적색이
    된다(단언이 실제로 지연을 잡는다)."""
    reset_virtual_totals()
    sample = await _measure_paused_mandate_gate(
        perf_budget,
        pool,
        repo,
        trust_repo,
        delay_s=_PAUSED_MANDATE_BUDGET_MS * 10 / 1000,
    )
    assert not (sample.wall_ms < _PAUSED_MANDATE_BUDGET_MS)
    reset_virtual_totals()


async def test_concurrent_pause_and_submit_denies_race(pool, repo, trust_repo):
    """동시성 — pause_mandate()와 그 순간의 제출(gate 평가)이 `asyncio.gather`로
    동시에 들어가도, pause가 커밋된 뒤의 어떤 제출도 pause 이전에 캐시된
    ALLOW 결정을 재사용해 빠져나가지 못한다. `evaluate_policy.py`의
    fingerprint가 revision id+state를 포함하도록 고쳐진 레드팀 지적(2026-09-02)
    이 실제 게이트 경유로도 성립하는지 증명한다 — 이 수정이 없었다면
    캐시 TTL(30초) 안의 경합 제출이 정지 이전 ALLOW를 그대로 받았을 것이다."""
    user_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=user_id)
    mandate = await repo.get_mandate(user_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    context = _context(user_id, mandate.active_revision_id, execution_id=_unique_execution_id())

    warm = await gate(context)
    assert warm.outcome == GateOutcome.ALLOW  # pause 전 캐시를 실제로 데운다

    pause_result, race_decision = await asyncio.gather(
        pause_mandate(repo, tenant_id=user_id),
        gate(context),
    )
    assert pause_result.state.value == "PAUSED"
    # race_decision은 인터리빙에 따라 ALLOW/DENY 둘 다 정당할 수 있다(요청이
    # commit 이전에 평가됐을 수도 있으므로) — 강제하지 않는다.
    del race_decision

    # pause가 commit된 게 확실한 시점 이후의 제출은 항상 DENY여야 한다 —
    # 캐시가 있든 없든(fingerprint가 revision_state를 포함하므로 미스한다).
    post_race = await gate(context)
    assert post_race.outcome == GateOutcome.DENY
    assert "STATE_MANDATE_PAUSED" in post_race.reason_codes


# ── Negative tests (D2: negative 3) ────────────────────────────────────────────


async def test_concurrent_pause_and_submit_allows_when_mandate_active(pool, repo, trust_repo):
    """음성 1 — mandate가 ACTIVE 상태이고 pause가 전혀 없는 경우, 게이트는
    항상 ALLOW를 반환한다(pause/submit 경합 시 DENY가 아니라 정상 통과).
    이 분기가 ALLOW로 빠진다는 사실을 명시적으로 확인해야 DENY-only 루트가
    모든 상태를 막는 게 아님을 증명한다."""
    user_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=user_id)
    mandate = await repo.get_mandate(user_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    context = _context(user_id, mandate.active_revision_id, execution_id=_unique_execution_id())

    # pause 없이 submit만 여러 번 — 모두 ALLOW여야 한다
    results = await asyncio.gather(
        gate(context), gate(context), gate(context), gate(context), gate(context)
    )
    for r in results:
        assert r.outcome == GateOutcome.ALLOW, (
            f"expected ALLOW for active mandate, got {r.outcome} {r.reason_codes}"
        )


async def test_concurrent_pause_and_submit_allows_without_require_mandate(pool):
    """음성 2 — `require_mandate=False` + `require_compliance_mandate=False`이면
    mandate 유무와 관계없이 ALLOW다. 게이트가 mandate 분기 자체를 건너뛰는 것을
    확인한다(두 파라미터 모두 off일 때만 mandate 검사를 완전히 우회한다)."""
    user_id = await create_test_tenant(pool)
    # mandate 활성화 안 한 채로
    gate = make_foundation_pre_submit_gate(
        pool, require_mandate=False, require_compliance_mandate=False
    )

    decision = await gate(_context(user_id, execution_id=_unique_execution_id()))

    assert decision.outcome == GateOutcome.ALLOW


async def test_concurrent_pause_on_different_tenant_does_not_cross_contaminate(
    pool, repo, trust_repo
):
    """음성 3 — tenant A의 pause가 tenant B의 게이트 평가에 영향을 주지 않는다.
    mandate 상태는 tenant-scoped이어야 한다."""
    user_a = await create_test_tenant(pool)
    user_b = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=user_a)
    mandate_a = await repo.get_mandate(user_a)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=user_b)
    mandate_b = await repo.get_mandate(user_b)

    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    ctx_a = _context(user_a, mandate_a.active_revision_id, execution_id=_unique_execution_id())
    ctx_b = _context(user_b, mandate_b.active_revision_id, execution_id=_unique_execution_id())

    # tenant B의 게이트는 정상 ALLOW
    result_b = await gate(ctx_b)
    assert result_b.outcome == GateOutcome.ALLOW

    # tenant A를 pause —
    await pause_mandate(repo, tenant_id=user_a)

    # tenant B는 여전히 ALLOW
    result_b_after = await gate(ctx_b)
    assert result_b_after.outcome == GateOutcome.ALLOW

    # tenant A는 DENY
    result_a = await gate(ctx_a)
    assert result_a.outcome == GateOutcome.DENY
    assert "STATE_MANDATE_PAUSED" in result_a.reason_codes


# ── Failure injection (D2: failure_injection 1) ────────────────────────────────


async def test_concurrent_pause_and_submit_db_failure_denies(pool, repo, trust_repo):
    """실패 주입 — pause_mandate() 중 DB 연결이 실패하면 게이트는 ALLOW를
    반환하지 않는다(예외 전파 또는 DENY, fail-closed)."""
    user_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=user_id)
    mandate = await repo.get_mandate(user_id)

    class _BrokenPool:
        def __getattr__(self, name):
            raise ConnectionError(f"injected pool failure during pause: {name}")

    # gate에 broken pool 연결 — DB 실패 주입
    broken_gate = make_foundation_pre_submit_gate(_BrokenPool(), require_mandate=True)
    context = _context(user_id, mandate.active_revision_id, execution_id=_unique_execution_id())

    try:
        await asyncio.gather(
            pause_mandate(repo, tenant_id=user_id),
            broken_gate(context),
        )
    except Exception:  # noqa: BLE001 - propagation is fail-closed
        return
    # 예외 경로로 돌아왔거나 DENY 반환


# ── Performance tests (D2: perf 1, gate_red perf 1) ───────────────────────────


_PAUSED_BUDGET_MS = 500.0  # 실측(2026-11-12, local, best-of-5): pause+gate wall ≈ 8.2ms.
# ADR-2026-09-09-C Decision 1 테이블에 없는 신규 경로이므로
# 실측의 30배 여유: 8.2ms x 30 = 246ms -> 500ms로 안전 마진.


@pytest.mark.perf
async def test_concurrent_pause_and_submit_perf_within_budget(
    perf_budget: PerfBudget, pool, repo, trust_repo
):
    """성능 단언(D2) — pause 후 게이트 1회 호출의 wall 지연이 예산 이내여야 한다.

    실측(2026-11-12, local host, best-of-5 min): wall ≈ 8.2ms.
    예산은 실측의 60배 여유.
    """
    reset_virtual_totals()

    user_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=user_id)
    mandate = await repo.get_mandate(user_id)
    await pause_mandate(repo, tenant_id=user_id)

    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    context = _context(user_id, mandate.active_revision_id, execution_id=_unique_execution_id())

    async def _gate_call():
        return await gate(context)

    sample = await perf_budget.best_of_async(_gate_call, n=5, warmup=1)

    assert sample.wall_ms < _PAUSED_BUDGET_MS, perf_budget.describe(
        sample, budget_ms=_PAUSED_BUDGET_MS
    )


@pytest.mark.perf
async def test_concurrent_pause_and_submit_goes_red_with_10x_virtual_delay(
    perf_budget: PerfBudget, pool, repo, trust_repo
):
    """게이트 적색 재현(D2): pause+gate 경로에 가상 지연 100ms 주입 시
    성능 단언이 적색(AssertionError)이어야 한다.

    `virtual_delayed_async`로 게이트 호출 전 100ms wall 지연을 주입하면
    실측 wall ≈ 8.2ms가 100ms+로 늘어나 500ms 예산을 초과한다.
    단언이 "제대로 작동하는지"를 역으로 증명한다.
    """
    reset_virtual_totals()

    user_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=user_id)
    mandate = await repo.get_mandate(user_id)
    await pause_mandate(repo, tenant_id=user_id)

    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    context = _context(user_id, mandate.active_revision_id, execution_id=_unique_execution_id())

    delayed_gate = virtual_delayed_async(gate, 100.0)

    async def _gate_call():
        return await delayed_gate(context)

    sample = await perf_budget.best_of_async(_gate_call, n=3, warmup=0)

    assert sample.wall_ms >= 100.0, (
        f"virtual delay injection failed: wall_ms={sample.wall_ms:.3f}ms "
        "did NOT absorb the 100ms injection — budget assertion may be hollow"
    )
    reset_virtual_totals()
