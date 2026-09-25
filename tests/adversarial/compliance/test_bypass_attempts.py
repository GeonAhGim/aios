"""CM-20 적대적 스위트 — 실 DB(`TEST_DATABASE_URL`) 대상, 10종 우회 시도 전부 REJECT.

Spec: docs/specs/L4_compliance_and_regulatory_v1.0.md#§9 CM-20 ("적대적 스위트
`tests/adversarial/compliance/*`(우회·변조·권위 분리), DoD: 전 케이스 차단").

이 파일은 옆의 네 파일(`test_no_bypass.py` CM-A1/A5, `test_no_mandate_reject.py`
mandate 3분기, `test_self_approval_blocked.py` CM-A3, `test_rule_purity.py`
CM-A2)과 `tests/foundation/integration/compliance/*`이 이미 증명한 시나리오를
반복하지 않는다 — 각 테스트는 그 파일들이 다루지 않는 별개의 우회 기법 하나씩을
겨냥한다: 멱등성 스코프 재사용, 캐시/지문 악용, 플래그 오독, WORM 변조 2종
(policy_bundle UPDATE / policy_decision DELETE), fail-closed 스냅샷, 동시성
경합 2종, 테넌트 경계(explain 애플리케이션 계층), explain() 드리프트. 전부
앞선 CM 리프(CM-4 WORM 트리거, CM-8 `evaluate_pre_trade`/
`evaluate_compliance_gate`, CM-13 `explain()`, mandate 승인 흐름)의 기존
포트를 그대로 재사용하고, 새 src 코드는 없다(test-only 리프).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import asyncpg
import pytest

import src.foundation.mandates.application.explain as explain_module
from src.foundation.mandates.application.activate_revision import (
    activate_revision as activate_revision_command,
)
from src.foundation.mandates.application.create_draft_mandate import create_draft_mandate
from src.foundation.mandates.application.evaluate_pre_trade import (
    ComplianceBundleInactiveError,
    evaluate_pre_trade,
)
from src.foundation.mandates.application.explain import (
    ExplainBundleDriftedError,
    ExplainDecisionNotFoundError,
    explain,
)
from src.foundation.mandates.application.pause_mandate import pause_mandate
from src.foundation.mandates.application.propose_amendment import propose_amendment
from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.services.oms.application.submit_order import submit_order
from src.services.oms.domain.errors import IdempotencyDigestMismatchError
from src.services.order_service.foundation_compliance import evaluate_compliance_gate
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.order_service.gate import OrderContext
from tests.adversarial.compliance.test_no_bypass import (
    _create_running_execution,
    _mandate_forbidding,
    _profile,
    _registry,
    _submit_cmd,
)
from tests.foundation.integration.mandates.conftest import default_rules
from tests.foundation.integration.risk_gate.conftest import activate_mandate_with_defaults
from tests.integration.oms.conftest import create_test_tenant, seed_entity_context


async def _active_tenant(pool, repo, trust_repo, **rule_overrides):
    """default_rules(forbidden_assets=["XYZ"]) 기반 활성 mandate 테넌트 —
    `tests/foundation/integration/compliance/test_evaluate_pre_trade.py`의
    동명 헬퍼와 동형(CM-8 포트 재사용, 새 규칙 없음)."""
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


# --- 1. 멱등성 스코프 재사용으로 금지 심볼 밀반입 -----------------------------


async def test_idempotency_scope_replay_cannot_smuggle_restricted_symbol(pool, repo, trust_repo):
    """우회 시도 1 — 허용 심볼(BTC/USDT)로 이미 승인받아 소비한 멱등 스코프
    (tenant/account/provider/strategy/version/execution/intent_seq/window)를
    그대로 재사용해, 두 번째 호출에서 심볼만 금지 목록(XYZ)으로 바꿔치기해
    컴플라이언스 게이트 재평가 자체를 건너뛰려는 시도. `command_digest`가
    symbol을 포함하므로 두 번째 호출은 `IdempotencyDigestMismatchError`로
    fail-closed 거부되어야 하고(EXISTING으로 오인돼 그대로 통과하면 안 됨),
    XYZ 주문은 생성되지 않아야 한다."""
    from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository

    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)
    await _mandate_forbidding(pool, repo, trust_repo, user_id, "XYZ")

    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    entity_repo = PostgresEntityRepository(pool)
    first_cmd = _submit_cmd(user_id, execution_id, "BTC/USDT")

    first = await submit_order(
        first_cmd,
        pool=pool,
        profile=_profile(),
        registry=_registry("BTC/USDT"),
        pre_submit_gate=gate,
        entity_context=entity_context,
        entity_repo=entity_repo,
    )
    assert first.status.value == "VALIDATED"

    replay_cmd = first_cmd.model_copy(
        update={"command_id": uuid4(), "trace_id": uuid4(), "symbol": "XYZ"}
    )
    with pytest.raises(IdempotencyDigestMismatchError):
        await submit_order(
            replay_cmd,
            pool=pool,
            profile=_profile(),
            registry=_registry("XYZ"),
            pre_submit_gate=gate,
            entity_context=entity_context,
            entity_repo=entity_repo,
        )

    async with pool.acquire() as conn:
        xyz_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1 AND symbol = 'XYZ'",
            execution_id,
        )
    assert xyz_count == 0


# --- 2. mandate 강화 직후 캐시 재사용으로 되돌리기 시도 ------------------------


async def test_mandate_tightening_invalidates_cached_allow_before_ttl_expiry(
    pool, repo, trust_repo
):
    """우회 시도 2 — 관대한 mandate(금지 목록 비어있음) 아래서 심볼 'ABC'로
    ALLOW 판정을 캐시(`(bundle_hash, inputs_hash)` 지문, TTL 30초)해 둔 뒤,
    운영자가 그 심볼을 금지 목록에 추가하는 개정을 즉시 활성화하고, TTL이
    끝나기 전에 같은 스냅샷으로 다시 평가를 시도해 오래된 ALLOW 캐시를
    재사용시키려는 시도. `evaluate_pre_trade`는 매 호출마다 mandate/revision을
    새로 읽고 지문이 번들 내용(=forbidden_assets 포함)을 담으므로, 개정 직후
    지문이 달라져 캐시가 무효화되고 새 DENY가 나야 한다."""
    tenant_id = await create_test_tenant(pool)
    draft = await create_draft_mandate(
        repo,
        tenant_id=tenant_id,
        subject_id=tenant_id,
        rules=default_rules(forbidden_assets=[]),
    )
    await activate_revision_command(
        repo,
        trust_repo,
        tenant_id=tenant_id,
        subject_id=tenant_id,
        revision_id=draft.id,
        reauthenticated=False,
    )

    allowed = await evaluate_pre_trade(
        repo,
        tenant_id=tenant_id,
        portfolio_id=None,
        snapshot={"symbol": "ABC"},
        now=datetime.now(timezone.utc),
    )
    assert allowed.verdict == ComplianceVerdict.ALLOW

    tightened = await propose_amendment(
        repo, tenant_id=tenant_id, rules=default_rules(forbidden_assets=["ABC"])
    )
    await activate_revision_command(
        repo,
        trust_repo,
        tenant_id=tenant_id,
        subject_id=tenant_id,
        revision_id=tightened.id,
        reauthenticated=False,
    )

    replay = await evaluate_pre_trade(
        repo,
        tenant_id=tenant_id,
        portfolio_id=None,
        snapshot={"symbol": "ABC"},
        now=datetime.now(timezone.utc),
    )
    assert replay.verdict == ComplianceVerdict.DENY
    assert "restricted_list" in replay.reason_codes
    assert replay.compliance_decision_id != allowed.compliance_decision_id


# --- 3. 비활성 번들을 permissive 플래그로 밀어 넣기 시도 -----------------------


async def test_paused_bundle_denies_even_with_permissive_compliance_mandate_flag(
    pool, repo, trust_repo
):
    """우회 시도 3 — `evaluate_compliance_gate`의 `require_compliance_mandate`
    플래그는 문서상 "mandate가 아예 없음"만 통과시킬 수 있다 — 이 시도는 그
    관대한 플래그(`False`)가 "번들이 있지만 PAUSED"까지 같이 눈감아 줄
    것으로 기대한다. `ComplianceBundleInactiveError`는 플래그와 무관하게
    항상 DENY로 번역되어야 한다 — 대조군으로 mandate가 아예 없는 테넌트는
    같은 `False` 플래그에서 실제로 통과함을 함께 확인해, 이 거부가 플래그
    오작동이 아니라 의도된 구분임을 보인다."""
    tenant_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=tenant_id)
    await pause_mandate(repo, tenant_id=tenant_id)

    context = OrderContext(
        user_id=tenant_id,
        execution_id=1,
        exchange="bitget",
        mandate_revision_id=None,
        symbol="BTC/USDT",
    )
    now = datetime.now(timezone.utc)

    permissive = await evaluate_compliance_gate(
        repo, context, require_compliance_mandate=False, now=now
    )
    assert permissive.allowed is False
    assert "CM_BUNDLE_INACTIVE" in permissive.reason_codes

    strict = await evaluate_compliance_gate(repo, context, require_compliance_mandate=True, now=now)
    assert strict.allowed is False
    assert "CM_BUNDLE_INACTIVE" in strict.reason_codes

    no_mandate_tenant = await create_test_tenant(pool)
    no_mandate_context = OrderContext(
        user_id=no_mandate_tenant,
        execution_id=1,
        exchange="bitget",
        mandate_revision_id=None,
        symbol="BTC/USDT",
    )
    control = await evaluate_compliance_gate(
        repo, no_mandate_context, require_compliance_mandate=False, now=now
    )
    assert control.allowed is True


# --- 4. policy_bundle 직접 변조로 규칙 완화 시도 -------------------------------


async def test_worm_trigger_blocks_policy_bundle_rule_hash_tamper(pool, repo, trust_repo):
    """우회 시도 4 — CM-A3(작성자≠승인자) 승인 절차를 통째로 건너뛰고, 이미
    컴파일된 `policy_bundle.rule_hash`를 DB에서 직접 다른 값으로 바꿔치기해
    새 mandate revision·승인 없이 규칙을 완화하려는 시도. CM-4가 붙인
    whole-row WORM 트리거(append-only, `c6a3d8f14b92`)가 `policy_bundle`
    UPDATE 자체를 막아야 한다."""
    tenant_id = await _active_tenant(pool, repo, trust_repo)
    decided = await evaluate_pre_trade(
        repo,
        tenant_id=tenant_id,
        portfolio_id=None,
        snapshot={"symbol": "XYZ"},
        now=datetime.now(timezone.utc),
    )
    stored = await repo.get_policy_decision(decided.compliance_decision_id)
    assert stored is not None
    bundle_before = await repo.get_bundle(stored.bundle_id)
    assert bundle_before is not None

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE policy_bundle SET rule_hash = 'tampered' WHERE id = $1",
                stored.bundle_id,
            )

    bundle_after = await repo.get_bundle(stored.bundle_id)
    assert bundle_after is not None
    assert bundle_after.rule_hash == bundle_before.rule_hash


# --- 5. policy_decision 삭제로 증거인멸 시도 -----------------------------------


async def test_worm_trigger_blocks_policy_decision_delete(pool, repo, trust_repo):
    """우회 시도 5 — DENY 판정을 UPDATE가 아니라 아예 삭제해 "판정이 없었던
    것"처럼 감사 흔적을 지우려는 증거인멸 시도. CM-4 WORM 트리거는
    whole-row 단위라 UPDATE뿐 아니라 DELETE도 막는다."""
    tenant_id = await _active_tenant(pool, repo, trust_repo)
    denied = await evaluate_pre_trade(
        repo,
        tenant_id=tenant_id,
        portfolio_id=None,
        snapshot={"symbol": "XYZ"},
        now=datetime.now(timezone.utc),
    )
    assert denied.verdict == ComplianceVerdict.DENY

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM policy_decision WHERE id = $1", denied.compliance_decision_id
            )

    still_there = await repo.get_policy_decision(denied.compliance_decision_id)
    assert still_there is not None
    assert still_there.outcome.value == "DENY"


# --- 6. symbol=None 스냅샷으로 규칙 스킵 시도 ----------------------------------


async def test_explicit_none_symbol_snapshot_fails_closed_not_silently_skipped(
    pool, repo, trust_repo
):
    """우회 시도 6 — 실행-시작 게이트(아직 구체적 주문 없음)에서는 `symbol`
    키 자체를 생략해 restricted_list 규칙을 건너뛰는 것이 정상 동작이다
    (`foundation_compliance._compliance_snapshot`). 이 시도는 그 틈을 노려
    진짜 주문 평가에서도 `symbol` 키는 넣되 값만 `None`으로 채워, 규칙이
    "필드 없음(스킵해도 됨)"과 "필드는 있지만 비어있음(위험)"을 혼동하길
    기대한다. `restricted_list.check`는 키 존재 여부가 아니라 값이 `None`인지로
    판단하므로, 이 스냅샷은 규칙이 실제로 평가되어 "필드 누락" fail-closed
    DENY를 내야 한다 — 조용히 통과하면 안 된다."""
    tenant_id = await _active_tenant(pool, repo, trust_repo)

    result = await evaluate_pre_trade(
        repo,
        tenant_id=tenant_id,
        portfolio_id=None,
        snapshot={"symbol": None},
        now=datetime.now(timezone.utc),
    )

    assert result.verdict == ComplianceVerdict.DENY
    assert "restricted_list" in result.reason_codes


# --- 7. 금지·허용 심볼 동시 평가로 지문 혼선 유도 시도 -------------------------


async def test_concurrent_restricted_and_allowed_evaluations_do_not_cross_contaminate(
    pool, repo, trust_repo
):
    """우회 시도 7 — 같은 테넌트 아래서 금지 심볼과 허용 심볼 주문 평가를
    정확히 동시에(같은 커넥션 풀·같은 캐시 조회 경합 안에서) 밀어 넣어, 두
    평가의 지문(fingerprint)/캐시 조회가 서로 뒤섞여 금지 심볼 쪽이 허용
    심볼의 ALLOW 판정을 가로채는 경합을 노리는 시도. `evaluate_pre_trade`는
    스냅샷마다 다른 지문을 쓰므로 어떤 인터리빙에서도 섞이면 안 된다."""
    tenant_id = await _active_tenant(pool, repo, trust_repo)
    now = datetime.now(timezone.utc)

    denied, allowed = await asyncio.gather(
        evaluate_pre_trade(
            repo, tenant_id=tenant_id, portfolio_id=None, snapshot={"symbol": "XYZ"}, now=now
        ),
        evaluate_pre_trade(
            repo,
            tenant_id=tenant_id,
            portfolio_id=None,
            snapshot={"symbol": "BTC/USDT"},
            now=now,
        ),
    )

    assert denied.verdict == ComplianceVerdict.DENY
    assert "restricted_list" in denied.reason_codes
    assert allowed.verdict == ComplianceVerdict.ALLOW
    assert denied.compliance_decision_id != allowed.compliance_decision_id


# --- 8. pause 커밋과 동시 제출로 비활성 직전 번들 노림 시도 --------------------


async def test_concurrent_pause_and_pretrade_evaluation_denies_race(pool, repo, trust_repo):
    """우회 시도 8 — `pause_mandate()` 커밋과 정확히 동시에 들어가는
    사전판정 요청이 pause 직전에 유효했던 ACTIVE 번들로 슬쩍 평가되길
    기대하는 경합 시도. `test_no_mandate_reject.py`의 수치 정책 경합과
    동형이지만, 여기서는 CM-8 컴플라이언스 계층 자신의
    `ComplianceBundleInactiveError` 분기가 독립적으로도 경합에 안전한지
    증명한다 — pause가 커밋된 게 확실한 시점 이후의 평가는 항상
    `ComplianceBundleInactiveError`여야 한다."""
    tenant_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(repo, trust_repo, tenant_id=tenant_id)

    warm = await evaluate_pre_trade(
        repo,
        tenant_id=tenant_id,
        portfolio_id=None,
        snapshot={"symbol": "BTC/USDT"},
        now=datetime.now(timezone.utc),
    )
    assert warm.verdict == ComplianceVerdict.ALLOW  # pause 전 캐시를 실제로 데운다

    async def _race_eval() -> None:
        try:
            await evaluate_pre_trade(
                repo,
                tenant_id=tenant_id,
                portfolio_id=None,
                snapshot={"symbol": "ETH/USDT"},
                now=datetime.now(timezone.utc),
            )
        except ComplianceBundleInactiveError:
            pass  # 인터리빙에 따라 ALLOW/DENY 둘 다 정당할 수 있다 — 강제하지 않는다.

    await asyncio.gather(pause_mandate(repo, tenant_id=tenant_id), _race_eval())

    with pytest.raises(ComplianceBundleInactiveError):
        await evaluate_pre_trade(
            repo,
            tenant_id=tenant_id,
            portfolio_id=None,
            snapshot={"symbol": "BTC/USDT"},
            now=datetime.now(timezone.utc),
        )


# --- 9. 교차 테넌트 판정 ID 추측으로 explain() 열람 시도 -----------------------


async def test_cross_tenant_explain_lookup_rejected_at_application_layer(pool, repo, trust_repo):
    """우회 시도 9 — 다른 테넌트의 `compliance_decision_id`를 알아낸(추측·유출)
    공격자가 `explain()`을 테넌트 스코프 없이 호출해 남의 판정 상세(규칙
    근거·평가 시각 등)를 열람하려는 시도. `tenant_id`를 함께 넘기면 남의
    판정은 진짜 미존재와 동일하게 `ExplainDecisionNotFoundError`여야 하고,
    같은 id를 정당한 소유 테넌트로 조회하면 여전히 성공해야 한다(테넌트
    필터가 실수로 전부를 막는 게 아니라는 대조)."""
    owner_id = await _active_tenant(pool, repo, trust_repo)
    attacker_id = await create_test_tenant(pool)

    decided = await evaluate_pre_trade(
        repo,
        tenant_id=owner_id,
        portfolio_id=None,
        snapshot={"symbol": "XYZ"},
        now=datetime.now(timezone.utc),
    )

    with pytest.raises(ExplainDecisionNotFoundError):
        await explain(repo, decided.compliance_decision_id, tenant_id=attacker_id)

    owned = await explain(repo, decided.compliance_decision_id, tenant_id=owner_id)
    assert owned.verdict == ComplianceVerdict.DENY


# --- 10. 번들 드리프트를 이용해 조용한 재판정을 노리는 시도 --------------------


async def test_explain_refuses_to_reproduce_when_bundle_hash_has_drifted(
    pool, repo, trust_repo, monkeypatch
):
    """우회 시도 10 — 컴파일러 로직이 조용히 바뀌었거나(공급망 변조 포함)
    저장된 `policy_bundle.rule_hash`가 오늘 재컴파일한 값과 달라진 상황을
    흉내내, `explain()`이 "그래도 같은 판정"이라며 조용히 재판정 결과를
    내놓게 만들려는 시도(§9 CM-13 DoD "no quiet re-judgment"). 드리프트가
    감지되면 재현이 아니라 `ExplainBundleDriftedError`로 fail-closed해야
    한다."""
    tenant_id = await _active_tenant(pool, repo, trust_repo)
    decided = await evaluate_pre_trade(
        repo,
        tenant_id=tenant_id,
        portfolio_id=None,
        snapshot={"symbol": "XYZ"},
        now=datetime.now(timezone.utc),
    )

    monkeypatch.setattr(explain_module, "compile_rule_hash", lambda revision: "drifted-hash")

    with pytest.raises(ExplainBundleDriftedError):
        await explain(repo, decided.compliance_decision_id)
