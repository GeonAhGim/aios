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


def _context(user_id, mandate_revision_id=None):
    return OrderContext(
        user_id=user_id,
        execution_id=1,
        exchange="bitget",
        mandate_revision_id=mandate_revision_id,
    )


async def test_no_mandate_at_all_rejects(pool):
    """무 mandate — tenant에 `portfolio_mandate` 행 자체가 없으면(H-1a
    resolver도 채울 게 없다) RISK_MANDATE_REQUIRED DENY."""
    user_id = await create_test_tenant(pool)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)

    decision = await gate(_context(user_id))

    assert decision.outcome == GateOutcome.DENY
    assert decision.reason_codes == ("RISK_MANDATE_REQUIRED",)

    async with pool.acquire() as conn:
        audit_row = await conn.fetchrow(
            "SELECT * FROM audit_log WHERE action_type = 'risk_gate.unmandated_submit' "
            "AND target_id = $1",
            "1",
        )
    assert audit_row is not None  # DENY 이전에도 감사 기록은 남는다


async def test_paused_mandate_rejects(pool, repo, trust_repo):
    """정지 mandate — active revision이 PAUSED 상태면 evaluate_policy가
    PAUSE_REQUIRED를 반환하고, 게이트는 이를 DENY(STATE_MANDATE_PAUSED)로
    번역한다(리스크 fence는 전부 깨끗해도 이 분기가 독립적으로 막는다)."""
    user_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=user_id)
    mandate = await repo.get_mandate(user_id)
    await pause_mandate(repo, tenant_id=user_id)

    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    decision = await gate(_context(user_id, mandate.active_revision_id))

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
        repo, trust_repo, tenant_id=user_id, subject_id=user_id,
        revision_id=proposed.id, reauthenticated=False,
    )

    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)
    decision = await gate(_context(user_id, expired_revision_id))

    assert decision.outcome == GateOutcome.DENY
    assert decision.reason_codes == ("RISK_MANDATE_REVISION_STALE",)


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
    context = _context(user_id, mandate.active_revision_id)

    warm = await gate(context)
    assert warm.outcome == GateOutcome.ALLOW  # pause 전 캐시를 실제로 데운다

    pause_result, race_decision = await asyncio.gather(
        pause_mandate(repo, tenant_id=user_id), gate(context),
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
