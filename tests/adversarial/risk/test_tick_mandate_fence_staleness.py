"""task-1806 — tick 경로에서 `pre_submit_check.evaluate_submission_gate()`에
넘기는 `mandate_revision_id`/`observed_fence`가 항상 None으로 죽어 있던
결함(P0-B 잔여분) 회귀 테스트.

Spec: 감사 2026-09-06 P0-B(task-1715/1806), R-36(task-1403 a2e2646),
R-33(task-99505ac) `fence_pairs_for`/`read_fence_snapshot` 재사용.

`evaluate_submission_gate`(tick.py가 실제로 호출하는 그 함수)를 실제
`make_foundation_pre_submit_gate` 뒤에서 호출해, tick 경로에 배선된 두
"관측-vs-현재" 비교가 죽은 코드가 아님을 증명한다:
1. 관측 fence가 현재보다 1 낮으면(stale) DENY(RISK_FENCE_STALE).
2. 실행이 바인딩된 mandate revision이 amendment로 superseded되면
   DENY(RISK_MANDATE_REVISION_STALE) — fence와 동일한 클래스의 결함이라
   task-1806이 함께 심었다(foundation_gate.py).
3. 둘 다 최신이면 ALLOW.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.mandates.application.activate_revision import (
    activate_revision as activate_revision_command,
)
from src.foundation.mandates.application.propose_amendment import propose_amendment
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.services.execution_loop.pre_submit_check import evaluate_submission_gate
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.order_service.gate import GateOutcome
from tests.adversarial.risk.conftest import fence_reader, seed_execution
from tests.foundation.integration.mandates.conftest import default_rules
from tests.foundation.integration.risk_gate.conftest import activate_mandate_with_defaults
from tests.integration.conftest import create_test_user

_SYMBOL = "BTC/USDT"
_SIDE = "BUY"
_QUANTITY = Decimal("0.01")


async def _bind_execution_to_revision(pool, execution_id: int, revision_id) -> None:
    """`strategy_executions.mandate_revision_id`(b3f7e0c1a4d5) — task-1806
    이전엔 이 컬럼을 읽는 호출부가 없었다(tick.py는 항상 None을 넘겼다).
    UI/API가 아직 이 컬럼을 채우는 경로가 없어 직접 UPDATE로 대신한다."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_executions SET mandate_revision_id = $1 WHERE id = $2",
            revision_id,
            execution_id,
        )


@pytest.fixture
async def setup(pool):
    user_id = await create_test_user(pool)
    execution_id = await seed_execution(pool, user_id)
    mandate_repo = PostgresMandateRepository(pool)
    trust_repo = PostgresTrustRepository(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=user_id)
    mandate = await mandate_repo.get_mandate(user_id)
    revision_id = mandate.active_revision_id
    await _bind_execution_to_revision(pool, execution_id, revision_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    read_fence = fence_reader(pool, user_id, execution_id)
    return user_id, execution_id, mandate_repo, revision_id, gate, read_fence


async def test_stale_observed_fence_denies_tick_submission(setup):
    user_id, execution_id, _mandate_repo, revision_id, gate, read_fence = setup
    current = await read_fence()
    stale_key = next(iter(current))
    stale_observed = dict(current)
    stale_observed[stale_key] -= 1  # 현재값보다 1 낮은 관측 F0 — DoD 지정 시나리오

    decision = await evaluate_submission_gate(
        gate,
        user_id=user_id,
        execution_id=execution_id,
        exchange="bitget",
        mandate_revision_id=revision_id,
        observed_fence=stale_observed,
        symbol=_SYMBOL,
        side=_SIDE,
        quantity=_QUANTITY,
    )

    assert decision.outcome == GateOutcome.DENY
    assert decision.reason_codes == ("RISK_FENCE_STALE",)


async def test_superseded_mandate_revision_denies_tick_submission(pool, setup):
    user_id, execution_id, mandate_repo, bound_revision_id, gate, read_fence = setup

    # 실행은 여전히 옛 revision에 바인딩된 채로, mandate는 (non-material)
    # amendment로 새 revision을 activate한다 — 옛 revision은 SUPERSEDED.
    proposed = await propose_amendment(
        mandate_repo, tenant_id=user_id, rules=default_rules(max_total_exposure_pct=50.0)
    )
    trust_repo = PostgresTrustRepository(pool)
    await activate_revision_command(
        mandate_repo,
        trust_repo,
        tenant_id=user_id,
        subject_id=user_id,
        revision_id=proposed.id,
        reauthenticated=False,
    )
    new_revision = await mandate_repo.get_mandate(user_id)
    assert new_revision.active_revision_id != bound_revision_id  # 전제 — 실제로 바뀌었다

    fresh_fence = await read_fence()
    decision = await evaluate_submission_gate(
        gate,
        user_id=user_id,
        execution_id=execution_id,
        exchange="bitget",
        mandate_revision_id=bound_revision_id,  # 실행에 남아 있는 옛(superseded) 값
        observed_fence=fresh_fence,
        symbol=_SYMBOL,
        side=_SIDE,
        quantity=_QUANTITY,
    )

    assert decision.outcome == GateOutcome.DENY
    assert decision.reason_codes == ("RISK_MANDATE_REVISION_STALE",)


async def test_matching_fence_and_mandate_revision_allows_tick_submission(setup):
    user_id, execution_id, _mandate_repo, revision_id, gate, read_fence = setup
    fresh_fence = await read_fence()

    decision = await evaluate_submission_gate(
        gate,
        user_id=user_id,
        execution_id=execution_id,
        exchange="bitget",
        mandate_revision_id=revision_id,
        observed_fence=fresh_fence,
        symbol=_SYMBOL,
        side=_SIDE,
        quantity=_QUANTITY,
    )

    assert decision.outcome == GateOutcome.ALLOW
    assert decision.reason_codes == ()
