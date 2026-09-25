"""14.3 — Strategy persistence and lifecycle wiring (StrategyBuilderService).

Spec: functional_design_doc_v1.20.md#FD-14.3, 9.9 (absolute principle), item 13 §13.5

FD-14.2 (condition composition → FSM compilation) is frontend+compiler territory,
outside this session's (backend-only) scope — this service starts from the point
of receiving an already-compiled FSMStrategyConfig JSON and persisting it.

9.9 absolute principle — the lifecycle must pass through the fixed order
(GENERATED→BACKTESTING→VALIDATING→STRESS_TESTING→RISK_REVIEW→PAPER_TRADING→
APPROVED→DEPLOYED→MONITORING→REVIEW→RETIRED) with no exceptions and no
skipping steps. REJECTED/FAILED are terminal states reachable from any point
in that order.

assert_executable() is the actual enforcement point for the FD-14.3 exception
scenario ("attempt to execute right after saving → system blocks it") — FD-16
(execution control panel, not yet built) must call this right before creating
strategy_executions.

The APPROVED transition is FD-15.3 mismatch-warning hook ②'s point (the
strategy owner's own risk_profile vs. that strategy's risk_level) — on a
mismatch without acknowledgment, the transition itself is blocked (FD-15.3
handling: "show warning + require explicit consent → proceed only after
consent").

Deviation (2026-09-01, gap found after app assembly): the "my strategies list"
lookup endpoint isn't specified anywhere in the spec, so the marketplace
listing registration screen (SellStrategyPage.tsx) had users type in
strategy_id/version by hand — list_strategies() fills that gap.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.core.strategy.condition_evaluator import _ATOMIC_RE as _CONDITION_ATOMIC_RE
from src.services.condition_compiler import ORDER_FILLED
from src.services.risk_matching import check_mismatch

LIFECYCLE_ORDER = (
    "GENERATED",
    "BACKTESTING",
    "VALIDATING",
    "STRESS_TESTING",
    "RISK_REVIEW",
    "PAPER_TRADING",
    "APPROVED",
    "DEPLOYED",
    "MONITORING",
    "REVIEW",
    "RETIRED",
)
TERMINAL_FAILURE_STATUSES = ("REJECTED", "FAILED")
EXECUTABLE_STATUSES = frozenset({"APPROVED", "DEPLOYED", "MONITORING"})


class StrategyLifecycleError(Exception):
    """FD-14.3 failure — save rejected or invalid state transition.
    VALIDATION_INVALID_FIELD(400)."""


class StrategyNotFoundError(StrategyLifecycleError):
    """Raised when `get_strategy()` finds no matching row or the caller isn't the
    owner — kept as a separate subclass so it maps to RESOURCE_NOT_FOUND(404)
    distinctly (exception_mapping.py's EXCEPTION_MAP is type-based, so leaving
    it as a plain `StrategyLifecycleError` would force save-time 409/400
    reasons and this 404 onto the same status code — same rationale as
    PLT-17's `ExchangeCredentialNotFoundError`)."""


def _validate_condition_syntax(condition: str) -> None:
    """L16 — validates `fsm_definition.transitions[].condition` against the same
    single grammar ConditionEvaluator uses (condition_evaluator.py, FROZEN):
    `_ATOMIC_RE` (`{key} {operator} {threshold}`, combined with AND/OR).
    `ConditionEvaluator._evaluate_atomic` only checks this grammar at execution
    time, so a syntax error saved as-is would surface only at execution
    (a possible violation of the 9.9 absolute principle) — this blocks it
    earlier, at save time. Whether `key` itself is registered in the indicator
    registry is out of scope here — raw market-data columns (e.g. `close`),
    as used in stop-loss conditions, are also valid keys that
    `ConditionEvaluator` accepts as-is (it just needs to be in `market_state`)."""
    if " AND " in condition:
        clauses = condition.split(" AND ")
    elif " OR " in condition:
        clauses = condition.split(" OR ")
    else:
        clauses = [condition]

    for clause in clauses:
        if _CONDITION_ATOMIC_RE.match(clause.strip()) is None:
            raise StrategyLifecycleError(f"fsm_definition 조건식 문법 오류: {clause!r}")


def _validate_fsm_definition(fsm_definition: dict[str, Any]) -> None:
    transitions = fsm_definition.get("transitions")
    if not transitions:
        return
    for transition in transitions:
        condition = transition.get("condition") if isinstance(transition, dict) else None
        if not condition or condition == ORDER_FILLED:
            continue
        _validate_condition_syntax(condition)


class SavedStrategy(BaseModel):
    strategy_id: str
    version: str
    lifecycle_status: str
    risk_warning: str | None = None


class StrategyDetail(BaseModel):
    strategy_id: str
    version: str
    owner_user_id: UUID
    target_asset: str
    market: str
    exchange: str
    lifecycle_status: str
    fsm_definition: dict[str, Any]


class StrategySummary(BaseModel):
    strategy_id: str
    version: str
    target_asset: str
    market: str
    exchange: str
    lifecycle_status: str
    created_at: datetime


class StrategyBuilderService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save_strategy(
        self,
        owner_user_id: UUID,
        strategy_id: str,
        version: str,
        *,
        target_asset: str,
        market: str,
        exchange: str,
        fsm_definition: dict[str, Any],
        author_agent: str = "user",
    ) -> SavedStrategy:
        _validate_fsm_definition(fsm_definition)
        async with self._pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT 1 FROM strategies WHERE strategy_id = $1 AND version = $2",
                strategy_id,
                version,
            )
            if existing is not None:
                raise StrategyLifecycleError("이미 존재하는 strategy_id/version입니다.")

            await conn.execute(
                """
                INSERT INTO strategies
                    (strategy_id, version, owner_user_id, target_asset, market, exchange,
                     fsm_definition, author_agent, lifecycle_status)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, 'GENERATED')
                """,
                strategy_id,
                version,
                owner_user_id,
                target_asset,
                market,
                exchange,
                json.dumps(fsm_definition),
                author_agent,
            )
        return SavedStrategy(
            strategy_id=strategy_id, version=version, lifecycle_status="GENERATED"
        )

    async def get_strategy(
        self, owner_user_id: UUID, strategy_id: str, version: str
    ) -> StrategyDetail:
        """FD-14 Draft "GET /strategies/{id}" — owner-only lookup (separate from
        FD-13.4's access-rights determination which also covers buyers; the
        editor only ever shows the owner's own work)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT strategy_id, version, owner_user_id, target_asset, market, exchange, "
                "lifecycle_status, fsm_definition FROM strategies "
                "WHERE strategy_id = $1 AND version = $2",
                strategy_id,
                version,
            )
        if row is None or row["owner_user_id"] != owner_user_id:
            raise StrategyNotFoundError("존재하지 않거나 접근 권한이 없는 전략입니다.")
        return StrategyDetail(
            strategy_id=row["strategy_id"],
            version=row["version"],
            owner_user_id=row["owner_user_id"],
            target_asset=row["target_asset"],
            market=row["market"],
            exchange=row["exchange"],
            lifecycle_status=row["lifecycle_status"],
            fsm_definition=json.loads(row["fsm_definition"]),
        )

    async def list_strategies(self, owner_user_id: UUID) -> list[StrategySummary]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT strategy_id, version, target_asset, market, exchange, "
                "lifecycle_status, created_at FROM strategies "
                "WHERE owner_user_id = $1 ORDER BY created_at DESC",
                owner_user_id,
            )
        return [StrategySummary(**dict(row)) for row in rows]

    async def transition_lifecycle(
        self,
        strategy_id: str,
        version: str,
        new_status: str,
        *,
        risk_warning_acknowledged: bool = False,
    ) -> SavedStrategy:
        if new_status not in LIFECYCLE_ORDER and new_status not in TERMINAL_FAILURE_STATUSES:
            raise StrategyLifecycleError(f"알 수 없는 생애주기 상태입니다: {new_status}")

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT s.lifecycle_status, s.risk_level, u.risk_profile "
                "FROM strategies s JOIN users u ON u.user_id = s.owner_user_id "
                "WHERE s.strategy_id = $1 AND s.version = $2",
                strategy_id,
                version,
            )
            if row is None:
                raise StrategyLifecycleError("존재하지 않는 전략입니다.")
            current = row["lifecycle_status"]

            if new_status not in TERMINAL_FAILURE_STATUSES:
                if current not in LIFECYCLE_ORDER:
                    raise StrategyLifecycleError(
                        f"{current} 상태에서는 더 이상 전이할 수 없습니다."
                    )
                current_idx = LIFECYCLE_ORDER.index(current)
                new_idx = LIFECYCLE_ORDER.index(new_status)
                if new_idx != current_idx + 1:
                    raise StrategyLifecycleError(
                        f"생애주기를 건너뛸 수 없습니다: {current} 다음은 "
                        f"{LIFECYCLE_ORDER[current_idx + 1]}이어야 합니다(요청: {new_status})."
                    )

            risk_warning = None
            if new_status == "APPROVED" and row["risk_profile"] is not None:
                risk_warning = check_mismatch(row["risk_profile"], row["risk_level"])
                if risk_warning is not None and not risk_warning_acknowledged:
                    raise StrategyLifecycleError(risk_warning)

            # Addresses red-team audit finding (docs/RED_TEAM_FINDINGS.md #17) —
            # this had the same "read, then write separately with no condition"
            # pattern as findings #04/05/08/09/16. Gate the UPDATE itself on the
            # lifecycle_status just read, so that if another request (e.g. an
            # admin's REJECTED verdict racing the automated pipeline's next
            # transition) committed first, this fails loudly instead of
            # silently overwriting it.
            updated = await conn.fetchrow(
                "UPDATE strategies SET lifecycle_status = $3, updated_at = now() "
                "WHERE strategy_id = $1 AND version = $2 AND lifecycle_status = $4 "
                "RETURNING strategy_id",
                strategy_id,
                version,
                new_status,
                current,
            )
            if updated is None:
                raise StrategyLifecycleError(
                    "다른 요청이 이 전략의 생애주기를 방금 이미 변경했습니다"
                    "(동시 처리 충돌) — 다시 조회 후 시도하세요."
                )
        return SavedStrategy(
            strategy_id=strategy_id,
            version=version,
            lifecycle_status=new_status,
            risk_warning=risk_warning if risk_warning_acknowledged else None,
        )


def assert_executable(lifecycle_status: str) -> None:
    if lifecycle_status not in EXECUTABLE_STATUSES:
        raise StrategyLifecycleError(
            f"현재 생애주기 단계({lifecycle_status})에서는 실행할 수 없습니다 — "
            f"{'/'.join(sorted(EXECUTABLE_STATUSES))} 상태여야 합니다."
        )
