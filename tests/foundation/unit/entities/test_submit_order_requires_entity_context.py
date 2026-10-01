"""FA-5 DoD(1) — `submit_order`가 `entity_context` 없이는 어떤 저장소 write도
시도하지 않는다는 negative test. 실 DB 없이(페이크 pool) — 가드가 어댑터
INSERT보다 먼저 걸린다는 것을 `pool.acquire()` 호출 횟수(카운터)로 단언한다.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-5 §9 DoD
"fund/portfolio 컨텍스트 없이 submit_order를 호출하면 도메인 예외로 거부되는
negative test 1건(어댑터 INSERT 호출 0회를 카운터로 단언)".
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import NoReturn, cast
from unittest.mock import NonCallableMagicMock, create_autospec
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide, OrderType
from src.exchanges.bitget.venue_profile import BITGET_SPOT_PROFILE
from src.foundation.entities.application.resolve_context import (
    EntityContextResolutionError,
    EntityRepository,
)
from src.foundation.entities.contracts.v1 import EntityContext
from src.services.oms.application.submit_order import submit_order
from src.services.oms.contracts.v1_commands import OrderIdempotencyScope, SubmitOrderCommand
from src.services.oms.domain.symbol_registry import SymbolRegistry
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext


def _command() -> SubmitOrderCommand:
    user_id = uuid4()
    scope = OrderIdempotencyScope(
        tenant_id=user_id,
        account_ref="acct-1",
        provider="bitget",
        strategy_id="s1",
        strategy_version="1.0.0",
        execution_id=1,
        intent_seq=1,
        window_start=datetime.now(timezone.utc),
    )
    return SubmitOrderCommand(
        command_id=uuid4(),
        trace_id=uuid4(),
        scope=scope,
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO,
        actor_subject_id=user_id,
        issued_at=datetime.now(timezone.utc),
    )


async def _allow_gate(context: OrderContext) -> GateDecision:
    return GateDecision(
        outcome=GateOutcome.ALLOW, decision_id=uuid4(), compliance_decision_id=uuid4()
    )


def _pool_spy() -> NonCallableMagicMock:
    """Pool-compatible spy: every acquisition fails before any database I/O."""
    pool: NonCallableMagicMock = create_autospec(asyncpg.Pool, instance=True, spec_set=True)
    pool.acquire.side_effect = AssertionError("entity guard must precede pool.acquire")
    return pool


def _entity_context(*, tenant_id: UUID) -> EntityContext:
    return EntityContext(
        tenant_id=tenant_id,
        legal_entity_id=uuid4(),
        fund_id=uuid4(),
        portfolio_id=uuid4(),
        sub_account_id=uuid4(),
    )


class _RaisingEntityRepo:
    """실패주입 대역 — `verify_entity_context`가 부르는 4개 조회 중 첫
    호출(`get_legal_entity`)에서 의존성 장애(예: DB 커넥션 단절)를 흉내
    낸다. entity_context 가드는 이 예외를 삼키지 않고 그대로 전파해야
    한다 — INSERT 시도(`pool.acquire()`)는 여전히 0회여야 fail-closed다."""

    async def get_legal_entity(self, tenant_id: UUID, entity_id: UUID) -> NoReturn:
        raise ConnectionError("entity_repo 의존성 장애 주입 — DB 커넥션 단절 시뮬레이션")

    async def get_fund(self, tenant_id: UUID, fund_id: UUID) -> NoReturn:
        raise AssertionError("get_legal_entity에서 이미 실패했어야 합니다 — 도달 불가")

    async def get_portfolio(self, tenant_id: UUID, portfolio_id: UUID) -> NoReturn:
        raise AssertionError("get_legal_entity에서 이미 실패했어야 합니다 — 도달 불가")

    async def get_sub_account(self, tenant_id: UUID, sub_account_id: UUID) -> NoReturn:
        raise AssertionError("get_legal_entity에서 이미 실패했어야 합니다 — 도달 불가")


async def test_submit_order_rejects_missing_entity_context_without_touching_adapter() -> None:
    pool = _pool_spy()

    with pytest.raises(EntityContextResolutionError):
        await submit_order(
            _command(),
            pool=pool,
            profile=BITGET_SPOT_PROFILE,
            registry=SymbolRegistry(),
            pre_submit_gate=_allow_gate,
            entity_context=cast(EntityContext, None),
            entity_repo=cast(EntityRepository, None),
        )

    pool.acquire.assert_not_called()


async def test_submit_order_rejects_cross_tenant_entity_context_without_touching_adapter() -> None:
    """negative test 2 — `entity_context.tenant_id`가 `cmd.scope.tenant_id`와
    다르면(교차 테넌트 위조 시도) INSERT 이전에 거부돼야 한다."""
    pool = _pool_spy()
    cmd = _command()
    foreign_context = _entity_context(tenant_id=uuid4())
    assert foreign_context.tenant_id != cmd.scope.tenant_id

    with pytest.raises(EntityContextResolutionError):
        await submit_order(
            cmd,
            pool=pool,
            profile=BITGET_SPOT_PROFILE,
            registry=SymbolRegistry(),
            pre_submit_gate=_allow_gate,
            entity_context=foreign_context,
            entity_repo=cast(EntityRepository, None),
        )

    pool.acquire.assert_not_called()


async def test_submit_order_rejects_missing_entity_repo_without_touching_adapter() -> None:
    """negative test 3 — task-1925: `entity_context`가 유효(같은 테넌트)해도
    `entity_repo`가 없으면 위조 재확인(`verify_entity_context`)을 할 수 없어
    거부돼야 한다."""
    pool = _pool_spy()
    cmd = _command()
    context = _entity_context(tenant_id=cmd.scope.tenant_id)

    with pytest.raises(EntityContextResolutionError):
        await submit_order(
            cmd,
            pool=pool,
            profile=BITGET_SPOT_PROFILE,
            registry=SymbolRegistry(),
            pre_submit_gate=_allow_gate,
            entity_context=context,
            entity_repo=cast(EntityRepository, None),
        )

    pool.acquire.assert_not_called()


async def test_submit_order_propagates_entity_repo_failure_without_touching_adapter() -> None:
    """실패주입 — `entity_repo`가 (DB 커넥션 단절 등으로) 예외를 던지면
    `submit_order`는 이를 삼켜 성공으로 위장하지 않고 그대로 전파해야
    하며, 그 시점에 `pool.acquire()`는 아직 호출되지 않아야 한다(fail-closed
    가드가 INSERT보다 먼저 걸린다)."""
    pool = _pool_spy()
    cmd = _command()
    context = _entity_context(tenant_id=cmd.scope.tenant_id)

    with pytest.raises(ConnectionError):
        await submit_order(
            cmd,
            pool=pool,
            profile=BITGET_SPOT_PROFILE,
            registry=SymbolRegistry(),
            pre_submit_gate=_allow_gate,
            entity_context=context,
            entity_repo=_RaisingEntityRepo(),
        )

    pool.acquire.assert_not_called()


def test_gate_red_repro_check_entity_context_flips_to_red_on_contextless_write(
    tmp_path, monkeypatch
):
    """게이트 적색 재현 — `scripts/check_entity_context.py`(FA-5 AST 정적
    검사, main() 종료코드 0=통과)가 `entity_context` 없이 저장소 write를
    호출하는 함수를 실제로 적색 처리하는지, CI가 직접 호출하는 `main()`
    자체의 종료코드로 증명한다. 가짜 `src` 트리를 만들어 `_REPO_ROOT`와
    `_TARGET_FILES`만 바꿔치기하므로 실제 저장소 파일은 건드리지 않는다."""
    import scripts.check_entity_context as gate

    target_rel = "src/services/oms/application/submit_order.py"
    app_dir = tmp_path / "src" / "services" / "oms" / "application"
    app_dir.mkdir(parents=True)
    target_file = app_dir / "submit_order.py"

    target_file.write_text(
        "async def submit_order(cmd, entity_context):\n    await pool.execute(cmd)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate, "_TARGET_FILES", (target_rel,))
    assert gate.main() == 0  # 대조군 — entity_context 인자가 있는 트리는 통과한다

    target_file.write_text(
        "async def submit_order(cmd):\n    await pool.execute(cmd)\n",
        encoding="utf-8",
    )
    assert gate.main() == 1  # entity_context 인자를 빼자 적색으로 뒤집힌다
