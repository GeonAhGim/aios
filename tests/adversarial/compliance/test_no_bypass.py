"""CM-8/CM-20 적대적 스위트 — 실 DB(`TEST_DATABASE_URL`) 대상.

Spec: docs/specs/L4_compliance_and_regulatory_v1.0.md#§9 CM-8/CM-20, CM-A1, CM-A5.

CM-A5 — 위임장(mandate) 규칙 위반은 리스크가 ALLOW여도(킬스위치 없음) 주문을
막는다: 리스크·컴플라이언스는 분리된 두 권위다(§0). CM-A1 — 컴플라이언스
판정 없이 주문이 제출되는 경로는 존재하지 않는다: (1) 실제 배선(`foundation_
gate.py`)에서 판정을 지우면 방금 막혔던 시나리오가 통과로 바뀐다는 실행
증거(배선 증명), (2) `submit_order` 자신도 게이트가 무엇을 반환하든
`compliance_decision_id`가 없는 ALLOW는 거부한다(2차 방어선).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

import src.services.order_service.foundation_gate as foundation_gate_module
from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide, OrderType
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.foundation.mandates.adapters.postgres_policy_repository import (
    PostgresPolicyRepositoryMixin,
)
from src.foundation.mandates.application.activate_revision import (
    activate_revision as activate_revision_command,
)
from src.foundation.mandates.application.create_draft_mandate import create_draft_mandate
from src.services.oms.application.submit_order import OrderSubmitDeniedError, submit_order
from src.services.oms.contracts.v1_commands import OrderIdempotencyScope, SubmitOrderCommand
from src.services.oms.domain.symbol_registry import SymbolRegistry
from src.services.oms.domain.venue_profile import TimeoutBudget, VenueCapabilityProfile
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.order_service.gate import GateDecision, GateOutcome
from tests.foundation.integration.mandates.conftest import default_rules
from tests.integration.oms.conftest import create_test_tenant, seed_entity_context


def _profile() -> VenueCapabilityProfile:
    return VenueCapabilityProfile(
        venue="bitget",
        asset_classes=[AssetClass.CRYPTO],
        order_types={OrderType.MARKET, OrderType.LIMIT},
        time_in_force={"GTC", "IOC"},
        supports_client_order_id=True,
        client_order_id_max_len=40,
        client_order_id_charset="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        id_policy="STABLE",
        supports_modify=True,
        supports_cancel="YES",
        supports_ws_orders=True,
        supports_batch=False,
        price_tick={},
        qty_lot={},
        min_notional={},
        rate_limits={},
        submit_timeout=TimeoutBudget(),
        query_timeout=TimeoutBudget(),
        market_hours=None,
        max_open_orders_per_symbol=20,
        verified="DOC_ONLY",
    )


def _registry(symbol: str) -> SymbolRegistry:
    reg = SymbolRegistry()
    reg.register(
        symbol,
        "bitget",
        symbol.replace("/", ""),
        tick=Decimal("0.1"),
        lot=Decimal("0.0001"),
        min_notional=Decimal("5"),
        quote_ccy="USDT",
    )
    return reg


async def _create_running_execution(pool, user_id: UUID) -> int:
    strategy_id = f"cm8-adv-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', '{}'::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', 100, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
        )
    return row["id"]


def _submit_cmd(user_id: UUID, execution_id: int, symbol: str) -> SubmitOrderCommand:
    scope = OrderIdempotencyScope(
        tenant_id=user_id,
        account_ref="acct-1",
        provider="bitget",
        strategy_id="s1",
        strategy_version="1.0.0",
        execution_id=execution_id,
        intent_seq=1,
        window_start=datetime.now(timezone.utc),
    )
    return SubmitOrderCommand(
        command_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
        scope=scope,
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO,
        actor_subject_id=user_id,
        issued_at=datetime.now(timezone.utc),
    )


async def _mandate_forbidding(pool, repo, trust_repo, tenant_id: UUID, symbol: str) -> None:
    draft = await create_draft_mandate(
        repo,
        tenant_id=tenant_id,
        subject_id=tenant_id,
        rules=default_rules(forbidden_assets=[symbol]),
    )
    await activate_revision_command(
        repo,
        trust_repo,
        tenant_id=tenant_id,
        subject_id=tenant_id,
        revision_id=draft.id,
        reauthenticated=False,
    )


async def test_restricted_symbol_denies_despite_no_risk_control(pool, repo, trust_repo):
    """CM-A5 — 킬스위치·fence stale 전부 없음(리스크는 ALLOW할 상황)인데도
    위임장이 금지한 심볼은 거부된다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)
    await _mandate_forbidding(pool, repo, trust_repo, user_id, "XYZ")

    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    submit_cmd = _submit_cmd(user_id, execution_id, "XYZ")

    with pytest.raises(OrderSubmitDeniedError) as exc_info:
        await submit_order(
            submit_cmd,
            pool=pool,
            profile=_profile(),
            registry=_registry("XYZ"),
            pre_submit_gate=gate,
            entity_context=entity_context,
            entity_repo=PostgresEntityRepository(pool),
        )
    assert "restricted_list" in exc_info.value.reason_codes

    async with pool.acquire() as conn:
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
    assert order_count == 0


async def test_allowed_symbol_passes_with_real_compliance_decision(pool, repo, trust_repo):
    """positive — 금지 목록에 없는 심볼은 같은 mandate 아래서도 통과하고,
    실제 policy_decision 행이 남는다(마커 id가 아니다)."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)
    await _mandate_forbidding(pool, repo, trust_repo, user_id, "XYZ")

    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    submit_cmd = _submit_cmd(user_id, execution_id, "BTC/USDT")

    result = await submit_order(
        submit_cmd,
        pool=pool,
        profile=_profile(),
        registry=_registry("BTC/USDT"),
        pre_submit_gate=gate,
        entity_context=entity_context,
        entity_repo=PostgresEntityRepository(pool),
    )

    assert result.status.value == "VALIDATED"
    async with pool.acquire() as conn:
        decision_count = await conn.fetchval(
            "SELECT count(*) FROM policy_decision WHERE tenant_id = $1 "
            "AND command_type = 'PRE_TRADE_COMPLIANCE'",
            user_id,
        )
    assert decision_count >= 1


async def test_removing_compliance_wiring_lets_forbidden_symbol_through(
    pool, repo, trust_repo, monkeypatch
):
    """CM-A1 배선 증명 — `foundation_gate.py`의 `evaluate_compliance_gate`
    호출을 "항상 ALLOW"로 치환하면, 방금 (1)에서 거부됐던 바로 그 시나리오가
    더 이상 거부되지 않는다. 즉 그 거부는 오직 이 한 번의 호출에서 나온다."""

    class _AlwaysAllowResult:
        allowed = True
        reason_codes: tuple[str, ...] = ()
        compliance_decision_id = uuid.uuid4()

    async def _stub_evaluate_compliance_gate(*args, **kwargs):
        return _AlwaysAllowResult()

    monkeypatch.setattr(
        foundation_gate_module, "evaluate_compliance_gate", _stub_evaluate_compliance_gate
    )

    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)
    await _mandate_forbidding(pool, repo, trust_repo, user_id, "XYZ")

    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    submit_cmd = _submit_cmd(user_id, execution_id, "XYZ")

    allowed = await submit_order(
        submit_cmd,
        pool=pool,
        profile=_profile(),
        registry=_registry("XYZ"),
        pre_submit_gate=gate,
        entity_context=entity_context,
        entity_repo=PostgresEntityRepository(pool),
    )
    assert allowed.status.value == "VALIDATED"


async def test_submit_order_rejects_allow_decision_missing_compliance_id(pool, repo, trust_repo):
    """CM-A1 2차 방어선 — `foundation_gate.py`가 아닌 어떤 다른(가짜) 게이트가
    `compliance_decision_id`를 채우지 않은 채 ALLOW를 반환해도, `submit_order`
    자신이 그 주문을 거부한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)

    async def _forged_allow_gate(context: object) -> GateDecision:
        return GateDecision(
            outcome=GateOutcome.ALLOW, decision_id=uuid.uuid4(), compliance_decision_id=None
        )

    submit_cmd = _submit_cmd(user_id, execution_id, "BTC/USDT")

    with pytest.raises(OrderSubmitDeniedError) as exc_info:
        await submit_order(
            submit_cmd,
            pool=pool,
            profile=_profile(),
            registry=_registry("BTC/USDT"),
            pre_submit_gate=_forged_allow_gate,
            entity_context=entity_context,
            entity_repo=PostgresEntityRepository(pool),
        )
    assert exc_info.value.reason_codes == ("CM_DECISION_ID_MISSING",)

    async with pool.acquire() as conn:
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
    assert order_count == 0


async def test_real_db_write_failure_during_compliance_check_blocks_order(
    pool, repo, trust_repo, monkeypatch
):
    """실패 주입(DB/네트워크 결함, DEEPEN task-2861) — 위
    `test_removing_compliance_wiring_lets_forbidden_symbol_through`은 판정
    함수 자체(`evaluate_compliance_gate`)를 always-ALLOW 스텁으로 치환하는
    비즈니스 로직 우회로, DEPTH 감사가 "배선 증명이지 DB/네트워크 결함
    주입이 아니다"라고 지적한 바로 그 패턴이다. 이 테스트는 판정 로직은
    전혀 건드리지 않고, `PostgresMandateRepository`가 실제로 쓰는 DB I/O
    경계 하나(`insert_policy_decision`)만 진짜 asyncpg 커넥션-단절 예외
    클래스(`ConnectionDoesNotExistError`)를 던지도록 만든다.
    `foundation_gate.py`의 `evaluate_compliance_gate` 호출부는 try/except로
    감싸여 있지 않으므로(코드 확인됨), 이 결함이 삼켜져 조용히 ALLOW로
    새지 않고 `submit_order` 호출자까지 그대로 전파되어(fail-closed) 주문이
    생성되지 않아야 한다 — 심지어 심볼 자체는 금지 목록에 없어(허용
    판정이었을) 상황에서도."""

    async def _broken_insert_policy_decision(self, decision):
        raise asyncpg.exceptions.ConnectionDoesNotExistError("simulated connection loss")

    monkeypatch.setattr(
        PostgresPolicyRepositoryMixin, "insert_policy_decision", _broken_insert_policy_decision
    )

    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)
    await _mandate_forbidding(pool, repo, trust_repo, user_id, "XYZ")

    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    submit_cmd = _submit_cmd(user_id, execution_id, "BTC/USDT")  # XYZ가 아니라 허용될 심볼

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await submit_order(
            submit_cmd,
            pool=pool,
            profile=_profile(),
            registry=_registry("BTC/USDT"),
            pre_submit_gate=gate,
            entity_context=entity_context,
            entity_repo=PostgresEntityRepository(pool),
        )

    async with pool.acquire() as conn:
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
    assert order_count == 0
