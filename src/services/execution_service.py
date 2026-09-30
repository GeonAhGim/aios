"""16.2 — Select execution exchange and mode (PAPER/LIVE) (ExecutionService.create_execution).

Spec: FD-16.2, 9.10, FD-10.1, FD-12, Section 06 §6.1, Section 02 §2.2

Zone boundary — This service only creates strategy_executions rows by specifying
capital allocation, exchange, and mode. The logic for actually deciding "buy/sell"
remains the exclusive responsibility of FD-8 (FROZEN), and this boundary is not crossed.

For mode=LIVE, Critical Risk approval (FD-10.1) is always required regardless of
automation level (9.10) — the automation level tracking itself needed to evaluate the
conditional trigger "if automation level is 1-3" does not yet exist in this system
(no separate leaf) — on safety grounds, we handle this more conservatively as "always
requires approval" (excessive approval requirements are safer than insufficient
safeguards).

There is no dedicated column in strategy_executions to link approval requests and
execution rows (design gap), so we embed execution_id in approval_requests.context
to establish the connection — 16.3 (execution control) reverse-references using this
value to check approval status.

16.3 — start/pause/set_max_drawdown/retire were moved to `execution_control.py`
to comply with P6 (300-line-per-file limit) — each method of this class is a thin
delegation that passes `self._pool` etc. to its counterpart function, so the public
contract (`ExecutionService.start()` etc.) remains unchanged.

16.6 — PAPER→LIVE conversion (convert_to_live): existing PAPER executions are not
terminated; history is preserved as-is (as basis for performance comparison), and a
new LIVE execution is created as a separate row (linked via converted_from_execution_id)
— we do not create a path where virtual positions magically convert to real positions
(preventing misunderstanding and errors at their source). We reuse create_execution()
directly to go through the 16.1/16.2 procedure again (capital allocation, exchange,
mode validation, LIVE approval) — approval cannot be skipped.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import asyncpg

from src.core.approval import service as approval
from src.core.loader.risk_policy_loader import RiskPolicy
from src.services import execution_control
from src.services.approval_settings_service import ApprovalSettingsService
from src.services.capital_allocation import validate_capital_allocation
from src.services.execution_types import (
    ExecutionControlError,
    ExecutionCreateError,
    ExecutionSummary,
)
from src.services.order_service.gate import PreSubmitGate
from src.services.strategy_builder_service import EXECUTABLE_STATUSES

__all__ = [
    "ExecutionControlError",
    "ExecutionCreateError",
    "ExecutionSummary",
    "ExecutionService",
]

KIS_EXCHANGE = "kis"
VALID_MODES = ("PAPER", "LIVE")


class ExecutionService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        risk_policy: RiskPolicy,
        *,
        pre_start_gate: PreSubmitGate,
        publish: approval.PublishFn | None = None,
    ) -> None:
        self._pool = pool
        self._risk_policy = risk_policy
        self._publish = publish
        # Comprehensive audit §6 wiring — reuse the type from order_service.gate as-is.
        # (Despite the name "order", the shape (evaluates one of tenant/execution/exchange/mandate
        # to yield ALLOW/DENY) is identical — order_service.foundation_gate.
        # make_foundation_pre_submit_gate() can create a callable and inject it directly here
        # and it will work. We do not create a new type again). EO-05(I-01) —
        # Remove default value to block gate omission at compile/type-check time.
        self._pre_start_gate = pre_start_gate

    async def create_execution(
        self,
        user_id: UUID,
        strategy_id: str,
        strategy_version: str,
        *,
        allocated_capital: Decimal,
        currency: str,
        exchange: str,
        mode: str,
        available_balance: Decimal,
    ) -> ExecutionSummary:
        if mode not in VALID_MODES:
            raise ExecutionCreateError(f"알 수 없는 실행 모드입니다: {mode}")
        if mode == "LIVE" and exchange == KIS_EXCHANGE:
            raise ExecutionCreateError("Phase 1은 암호화폐 거래소만 실거래 가능합니다.")

        async with self._pool.acquire() as conn:
            strategy = await conn.fetchrow(
                "SELECT lifecycle_status, certified_badge FROM strategies "
                "WHERE strategy_id = $1 AND version = $2",
                strategy_id,
                strategy_version,
            )
            if strategy is None:
                raise ExecutionCreateError("존재하지 않는 전략입니다.")
            if strategy["lifecycle_status"] not in EXECUTABLE_STATUSES:
                raise ExecutionCreateError(
                    "APPROVED 이상 상태에서만 실행 설정이 가능합니다"
                    f"(현재: {strategy['lifecycle_status']})."
                )

            credential = await conn.fetchval(
                "SELECT 1 FROM exchange_credentials "
                "WHERE user_id = $1 AND exchange = $2 AND is_active = true",
                user_id,
                exchange,
            )
            if credential is None:
                raise ExecutionCreateError(f"{exchange}에 연동된 자격증명이 없습니다.")

            validate_capital_allocation(
                allocated_capital,
                available_balance,
                certified_badge=strategy["certified_badge"],
                policy=self._risk_policy.strategy_allocation,
            )

            row = await conn.fetchrow(
                "INSERT INTO strategy_executions "
                "(strategy_id, strategy_version, user_id, exchange, mode, "
                " allocated_capital, currency) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id, status",
                strategy_id,
                strategy_version,
                user_id,
                exchange,
                mode,
                allocated_capital,
                currency,
            )
            execution_id = row["id"]

        approval_request_id = None
        if mode == "LIVE":
            settings = await ApprovalSettingsService(self._pool).get(user_id)
            request = await approval.create_request(
                self._pool,
                scope="USER",
                user_id=user_id,
                trigger_source="execution_high_allocation",
                requested_action="START_LIVE_EXECUTION",
                context={
                    "execution_id": execution_id,
                    "allocated_capital": allocated_capital,
                },
                approval_mode=settings.mode,
                publish=self._publish,
            )
            approval_request_id = request.id

        return ExecutionSummary(
            id=execution_id,
            status=row["status"],
            mode=mode,
            exchange=exchange,
            allocated_capital=allocated_capital,
            approval_request_id=approval_request_id,
        )

    async def start(self, execution_id: int, user_id: UUID) -> ExecutionSummary:
        return await execution_control.start(
            self._pool, self._pre_start_gate, execution_id, user_id
        )

    async def pause(
        self,
        execution_id: int,
        *,
        paused_by: str = "USER",
        user_id: UUID | None = None,
    ) -> ExecutionSummary:
        return await execution_control.pause(
            self._pool, execution_id, paused_by=paused_by, user_id=user_id
        )

    async def set_max_drawdown(
        self, execution_id: int, user_id: UUID, max_drawdown_pct: Decimal | None
    ) -> ExecutionSummary:
        """ZuluTrade-style "risk management" (ZuluGuard) — when a per-execution loss limit (%)
        is set, risk_guard_service.py::evaluate_all_running() periodically compares realized +
        unrealized P&L to this limit and automatically pauses with paused_by='SAFETY_LAYER' if
        exceeded. Set to None to disable the guard (default behavior)."""
        return await execution_control.set_max_drawdown(
            self._pool, execution_id, user_id, max_drawdown_pct
        )

    async def retire(
        self, execution_id: int, user_id: UUID, *, liquidation: str = "KEEP_POSITIONS"
    ) -> ExecutionSummary:
        return await execution_control.retire(
            self._pool, execution_id, user_id, liquidation=liquidation
        )

    async def convert_to_live(
        self,
        user_id: UUID,
        source_execution_id: int,
        *,
        allocated_capital: Decimal,
        currency: str,
        exchange: str,
        available_balance: Decimal,
    ) -> ExecutionSummary:
        async with self._pool.acquire() as conn:
            source = await conn.fetchrow(
                "SELECT user_id, strategy_id, strategy_version, mode "
                "FROM strategy_executions WHERE id = $1",
                source_execution_id,
            )
            if source is None:
                raise ExecutionControlError("존재하지 않는 실행입니다.")
            if source["user_id"] != user_id:
                raise ExecutionControlError("본인의 실행만 전환할 수 있습니다.")
            if source["mode"] != "PAPER":
                raise ExecutionControlError("PAPER 모드 실행만 실매매로 전환할 수 있습니다.")

        result = await self.create_execution(
            user_id,
            source["strategy_id"],
            source["strategy_version"],
            allocated_capital=allocated_capital,
            currency=currency,
            exchange=exchange,
            mode="LIVE",
            available_balance=available_balance,
        )

        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE strategy_executions SET converted_from_execution_id = $2 WHERE id = $1",
                result.id,
                source_execution_id,
            )
        return result
