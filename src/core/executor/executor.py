"""FD-8.4 — 주문 실행 트리거 (Executor).

Spec: 기능설계문서_v1.21.md#FD-8.4, 03_core_modules_v1.1.md#§3.8, ADR-2026-08-29-E

판단하지 않는다 — 승인된 AllocationDecision+RiskCheckResult만 받아 FD-4를
호출한다. `risk_result.approved=False`인 건이 이 함수에 도달하면 그
자체가 상위 로직 버그다(방어적 assert 유지).

LIVE 하드 가드 — mode != 'PAPER'이면 무조건 FrozenZoneLiveModeBlockedError.
정책 문서상 금지가 아니라 실행되는 코드 자체의 차단(ADR-2026-08-29-E).
15.6-D 조건 2(실계정 MFA·이중승인) 충족 후 별도 ADR 없이는 이 가드를
약화시키지 않는다.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from src.core.exceptions import FrozenZoneLiveModeBlockedError, FrozenZonePaperAdapterBlockedError
from src.core.portfolio.models import AllocationDecision
from src.core.risk.models import RiskCheckResult
from src.core.validator.order_validator import validate_order_params
from src.data.models.base import AssetClass
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.common.adapter import ExchangeAdapter
from src.services.condition_compiler import ORDER_FILLED
from src.services.order_service import OrderSubmissionError, submit_order
from src.services.order_service.submit import PublishFn

logger = logging.getLogger(__name__)

FsmStateWriter = Callable[[int, FSMState, FSMState], Awaitable[None]]


def next_fsm_state_after_fill(fsm_config: FSMStrategyConfig, pending_state: FSMState) -> FSMState:
    """PENDING 상태(BUY_ORDER_PENDING/SELL_ORDER_PENDING/STOP_LOSS)에서
    ORDER_FILLED 예약 리터럴 전이의 도착 상태를 찾는다 — 이게 이 전이가
    실제로 반영되는 유일한 지점(FD-8.4 처리단계 5)."""
    for transition in fsm_config.transitions:
        if transition.from_state == pending_state and transition.condition == ORDER_FILLED:
            return transition.to_state
    raise ValueError(f"{pending_state}에서 ORDER_FILLED로 나가는 전이가 없습니다 — FSM 정의 오류")


class Executor:
    async def execute(
        self,
        allocation: AllocationDecision,
        risk_result: RiskCheckResult,
        adapter: ExchangeAdapter,
        *,
        execution_id: int,
        user_id: UUID,
        strategy_version: str,
        mode: str,
        side: OrderSide,
        pending_fsm_state: FSMState,
        fsm_config: FSMStrategyConfig,
        fsm_state_writer: FsmStateWriter,
        publish: PublishFn | None = None,
        pool: asyncpg.Pool,
    ) -> Order:
        if not risk_result.approved:
            raise ValueError("Risk 미승인 건은 Executor에 도달해서는 안 됨 — 상위 로직 오류")

        if mode != "PAPER":
            raise FrozenZoneLiveModeBlockedError(
                f"LIVE 모드 실행(execution_id={execution_id})은 15.6-D 조건 2"
                "(실계정 MFA·이중승인) 충족 및 별도 ADR 전까지 차단됩니다"
                "(ADR-2026-08-29-E)."
            )
        if not adapter.is_paper_trading or not adapter.is_sandboxed:
            # 레드팀 감사(2026-09-01-08) 반영 — DB의 mode='PAPER'만으로는
            # 잘못 구성된 real adapter(예: demo_mode=False로 생성된
            # BitgetAdapter)를 통한 실주문을 막지 못한다. adapter 스스로
            # sandbox 바인딩을 증명하지 못하면 mode 값과 무관하게 차단한다
            # — DB mode, adapter의 두 독립 신호 중 하나라도 걸리면 거부.
            raise FrozenZonePaperAdapterBlockedError(
                "PAPER 실행에는 sandbox로 구성된 거래소 adapter만 주입할 수 있습니다."
            )

        client_order_id = (
            f"{execution_id}:{pending_fsm_state.value}:{datetime.now(timezone.utc).isoformat()}"
        )
        order = Order(
            client_order_id=client_order_id,
            strategy_id=allocation.strategy_id,
            strategy_version=strategy_version,
            execution_id=execution_id,
            symbol=allocation.symbol,
            exchange=adapter.get_capabilities().exchange_name,
            side=side,
            order_type=OrderType.MARKET,
            quantity=allocation.approved_quantity,
            asset_class=AssetClass.CRYPTO,
        )

        validation = validate_order_params(order)
        if not validation.is_valid:
            logger.critical(
                "Executor: FD-8.2가 만든 Allocation이 주문 형식 자체를 위반했습니다 "
                "(execution_id=%s, errors=%s) — Executor가 아니라 PortfolioEngine 버그입니다.",
                execution_id,
                validation.errors,
            )
            raise OrderSubmissionError(f"주문 파라미터 검증 실패: {validation.errors}")

        try:
            submitted = await submit_order(
                order, user_id=user_id, adapter=adapter, pool=pool, publish=publish
            )
        except Exception:
            logger.critical(
                "Executor: 주문 전송 실패(execution_id=%s, client_order_id=%s) — "
                "fsm_state(%s)는 되돌리지 않습니다. 반복 실패 시 Watchdog가 감지합니다.",
                execution_id,
                client_order_id,
                pending_fsm_state.value,
                exc_info=True,
            )
            raise

        if submitted.status == OrderStatus.FILLED:
            next_state = next_fsm_state_after_fill(fsm_config, pending_fsm_state)
            await fsm_state_writer(execution_id, pending_fsm_state, next_state)

        return submitted
