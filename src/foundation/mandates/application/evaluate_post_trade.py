"""L4_compliance_and_regulatory_v1.0.md#9 CM-11 -- post-trade/EOD batch determination for fills.

CM-9 (`domain/rules/{short_sale,wash_trade}.py`) and CM-10 (`domain/market_abuse.py`) rules are
only called, never reimplemented -- if a crossing-price/wash-window/spoofing-ratio formula
reappears here, that is a defect. A DENY leads to `KillSwitchService.activate()` (TENANT scope);
because of R-40/I3 (one safety_control insert call site, `postgres_repository.py`) this file
never writes to that table directly -- the tenant's next order is rejected by the already-wired
ACTIVE control check in `foundation_gate.py`.

Reduced scope (unverified, follow-up leaf): `position_qty` is the current-quantity snapshot from
`positions` (not a per-fill running balance); `borrow_available_qty` is always 0 (LA-25
`pos_borrow_position` has no lookup adapter yet); `market_close_at` is UTC midnight, not the
actual exchange close.

Idempotency (DoD (c)): instead of `safety_control.idempotency_digest` (changing
`insert_safety_control()`'s signature is outside this file's scope), this file checks for an
existing ACTIVE control for the same reason (tenant+rule_code+business_date) before activating.
That check-then-activate is a read-then-write race under concurrent batch instances; DEEPEN
task-2865 closes it the way `risk_guard_service.py` closes its scope_ref race -- a
`pg_advisory_xact_lock` on `(tenant_id, reason)` held for the whole check+activate
(`_activate_if_not_active`).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.domain import market_abuse
from src.foundation.mandates.domain.evaluator import evaluate_bundle
from src.foundation.mandates.domain.market_abuse import AbuseHit
from src.foundation.mandates.domain.rule_bundle import RuleBundle, RuleSpec
from src.foundation.mandates.domain.rules import short_sale, wash_trade
from src.foundation.risk_gate.domain.models import SafetyScope
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.services.safety.kill_switch_service import KillSwitchService

logger = logging.getLogger(__name__)

# watchdog_process.py의 WATCHDOG_SYSTEM_ACTOR_ID(e5a8c5d4f6b7)를 liquidation_executor.py처럼 재정의.
SYSTEM_ACTOR_ID = UUID("00000000-0000-0000-0000-000000000002")

_CM9_BUNDLE_VERSION = "cm11.post_trade.cm9/1"
_REASON_PREFIX = "COMPLIANCE"

_TERMINAL_ORDER_STATUSES = ("FILLED", "REJECTED", "CANCELLED", "EXPIRED", "FAILED")


@dataclass(frozen=True)
class PostTradeViolation:
    rule_code: str
    message: str
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PostTradeBatchReport:
    business_date: date
    tenants_evaluated: int
    blocked: dict[UUID, tuple[str, ...]]
    warnings: dict[UUID, tuple[str, ...]]


def _cm9_bundle(rule_params: Mapping[str, Mapping[str, Any]]) -> RuleBundle:
    # fmt: off
    return RuleBundle(
        version=_CM9_BUNDLE_VERSION,
        rules=(
            RuleSpec(rule_id=short_sale.RULE_ID, params=rule_params.get("short_sale", {}),
                     check=short_sale.check),
            RuleSpec(rule_id=wash_trade.RULE_ID, params=rule_params.get("wash_trade", {}),
                     check=wash_trade.check),
        ),
    )
    # fmt: on


def evaluate_tenant_day(
    *,
    fill_snapshots: Sequence[Mapping[str, Any]],
    market_abuse_window: Mapping[str, Any],
    rule_params: Mapping[str, Mapping[str, Any]],
    now: datetime,
) -> tuple[list[PostTradeViolation], list[PostTradeViolation]]:
    """Pure evaluation (one tenant's day), no I/O or clock reads. Both CM-9 rules are always DENY
    on a hit; the three CM-10 detections are WARN (only `DATA_MISSING` is DENY, I-02) -- per §3
    "WARN passes through but is recorded". Deduplicated by rule/pattern id."""
    bundle = _cm9_bundle(rule_params)
    blocking: dict[str, PostTradeViolation] = {}
    warnings: dict[str, PostTradeViolation] = {}

    def _record(
        rule_code: str, severity: ComplianceVerdict, message: str, evidence: Mapping[str, Any]
    ) -> None:
        target = blocking if severity == ComplianceVerdict.DENY else warnings
        target[rule_code] = PostTradeViolation(
            rule_code=rule_code, message=message, evidence=dict(evidence)
        )

    for snapshot in fill_snapshots:
        decision = evaluate_bundle(bundle, snapshot, now=now)
        for hit in decision.rule_hits:
            _record(hit.rule_id, hit.severity, hit.message, hit.evidence)

    try:
        abuse_hits: list[AbuseHit] = market_abuse.detect(
            market_abuse_window, rule_params.get("market_abuse", {})
        )
    except Exception:  # noqa: BLE001 -- DoD (d): an exception must block, not vanish
        # fmt: off
        abuse_hits = [AbuseHit(
            pattern_id="market_abuse.evaluation_exception", severity=ComplianceVerdict.DENY,
            reason_code="EVALUATION_EXCEPTION",
            message="market_abuse.detect() raised -- fail-closed block", evidence={},
        )]
        # fmt: on
    for abuse_hit in abuse_hits:
        _record(abuse_hit.pattern_id, abuse_hit.severity, abuse_hit.message, abuse_hit.evidence)

    return list(blocking.values()), list(warnings.values())


def _violation_reason(rule_code: str, business_date: date) -> str:
    return f"{_REASON_PREFIX}:{rule_code}:{business_date.isoformat()}"


async def _tenants_with_fills(
    pool: asyncpg.Pool, day_start: datetime, day_end: datetime
) -> list[UUID]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT o.user_id FROM fills f "
            "JOIN orders o ON o.order_id = f.order_id "
            "WHERE f.venue_ts >= $1 AND f.venue_ts < $2",
            day_start,
            day_end,
        )
    return [row["user_id"] for row in rows]


async def _load_tenant_window(
    pool: asyncpg.Pool, tenant_id: UUID, day_start: datetime, day_end: datetime
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Assembles the CM-9 fill snapshots and CM-10 window from `fills`/`orders`/`positions`
    (see the module docstring for reduced scope)."""
    async with pool.acquire() as conn:
        fill_rows = await conn.fetch(
            "SELECT f.id, f.side, f.quantity, f.price, f.symbol, f.venue_ts "
            "FROM fills f JOIN orders o ON o.order_id = f.order_id "
            "WHERE o.user_id = $1 AND f.venue_ts >= $2 AND f.venue_ts < $3 "
            "ORDER BY f.venue_ts",
            tenant_id,
            day_start,
            day_end,
        )
        order_rows = await conn.fetch(
            "SELECT side, price, symbol, status, quantity, updated_at FROM orders "
            "WHERE user_id = $1 AND (status != ALL($2::text[]) "
            "OR (updated_at >= $3 AND updated_at < $4))",
            tenant_id,
            list(_TERMINAL_ORDER_STATUSES),
            day_start,
            day_end,
        )
        position_rows = await conn.fetch(
            "SELECT symbol, quantity FROM positions WHERE user_id = $1", tenant_id
        )

    # fmt: off
    positions_by_symbol = {row["symbol"]: row["quantity"] for row in position_rows}
    open_orders_by_symbol: dict[str, list[dict[str, Any]]] = {}
    abuse_orders: list[dict[str, Any]] = []
    for row in order_rows:
        symbol = row["symbol"]
        if row["status"] not in _TERMINAL_ORDER_STATUSES:
            open_orders_by_symbol.setdefault(symbol, []).append({
                "tenant_id": tenant_id, "instrument": symbol,
                "side": row["side"], "price": row["price"],
            })
        canceled_at = row["updated_at"] if row["status"] == "CANCELLED" else None
        abuse_orders.append({
            "tenant_id": tenant_id, "instrument_id": symbol, "qty": row["quantity"],
            "submitted_at": day_start, "canceled_at": canceled_at,
        })

    fill_snapshots: list[dict[str, Any]] = []
    abuse_fills: list[dict[str, Any]] = []
    for row in fill_rows:
        symbol = row["symbol"]
        fill_snapshots.append({
            "tenant_id": tenant_id, "instrument": symbol, "side": row["side"],
            "order_qty": row["quantity"],
            "position_qty": positions_by_symbol.get(symbol, Decimal("0")),
            "borrow_available_qty": Decimal("0"), "order_price": row["price"],
            "open_orders": open_orders_by_symbol.get(symbol, []),
        })
        abuse_fills.append({
            "fill_id": str(row["id"]), "tenant_id": tenant_id, "instrument_id": symbol,
            "owner_id": tenant_id, "side": row["side"], "qty": row["quantity"],
            "executed_at": row["venue_ts"],
        })

    market_abuse_window = {
        "fills": abuse_fills, "orders": abuse_orders, "market_close_at": day_end,
    }
    return fill_snapshots, market_abuse_window
    # fmt: on


async def _has_active_control(repo: RiskGateRepository, tenant_id: UUID, reason: str) -> bool:
    controls = await repo.list_active_controls(tenant_id=tenant_id)
    return any(c.scope == SafetyScope.TENANT and c.reason == reason for c in controls)


async def _activate_if_not_active(
    pool: asyncpg.Pool,
    kill_switch: KillSwitchService,
    risk_gate_repo: RiskGateRepository,
    tenant_id: UUID,
    reason: str,
) -> bool:
    """D3 다중 인스턴스 -- `risk_guard_service.py`의 scope_ref advisory lock과 같은 패턴으로
    `(tenant_id, reason)` 키의 `pg_advisory_xact_lock`을 check+activate 전체 구간 동안 쥐어
    다른 배치 인스턴스의 끼어들기를 막는다. True는 이번 호출이 실제로 활성화했음을 뜻한다."""
    # fmt: off
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))", str(tenant_id), reason
        )
        if await _has_active_control(risk_gate_repo, tenant_id, reason):
            return False
        await kill_switch.activate(
            scope=SafetyScope.TENANT, scope_ref=str(tenant_id), reason=reason,
            actor_subject_id=SYSTEM_ACTOR_ID, actor_is_admin=True, trace_id=uuid4(),
        )
    return True
    # fmt: on


async def run_daily_post_trade_batch(
    pool: asyncpg.Pool,
    kill_switch: KillSwitchService,
    risk_gate_repo: RiskGateRepository,
    *,
    business_date: date,
    now: datetime,
    rule_params: Mapping[str, Mapping[str, Any]] | None = None,
) -> PostTradeBatchReport:
    """§9 CM-11 public entry point -- called periodically by
    `background_loops.py`."""
    # fmt: off
    resolved_params = rule_params or {}
    day_start = datetime.combine(business_date, time.min, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    tenant_ids = await _tenants_with_fills(pool, day_start, day_end)
    blocked: dict[UUID, tuple[str, ...]] = {}
    warnings: dict[UUID, tuple[str, ...]] = {}

    for tenant_id in tenant_ids:
        fill_snapshots, market_abuse_window = await _load_tenant_window(
            pool, tenant_id, day_start, day_end
        )
        blocking, tenant_warnings = evaluate_tenant_day(
            fill_snapshots=fill_snapshots, market_abuse_window=market_abuse_window,
            rule_params=resolved_params, now=now,
        )
        if tenant_warnings:
            warnings[tenant_id] = tuple(v.rule_code for v in tenant_warnings)
        if not blocking:
            continue

        activated: list[str] = []
        for violation in blocking:
            reason = _violation_reason(violation.rule_code, business_date)
            newly_activated = await _activate_if_not_active(
                pool, kill_switch, risk_gate_repo, tenant_id, reason
            )
            if newly_activated:
                logger.warning(
                    "evaluate_post_trade: tenant=%s rule=%s business_date=%s -- COMPLIANCE 차단",
                    tenant_id, violation.rule_code, business_date,
                )
            activated.append(violation.rule_code)
        blocked[tenant_id] = tuple(activated)

    return PostTradeBatchReport(
        business_date=business_date, tenants_evaluated=len(tenant_ids),
        blocked=blocked, warnings=warnings,
    )
    # fmt: on
