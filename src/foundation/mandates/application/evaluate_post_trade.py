"""L4_compliance_and_regulatory_v1.0.md#9 CM-11 -- 체결·일마감 사후 배치 판정.

CM-9(`domain/rules/{short_sale,wash_trade}.py`)·CM-10(`domain/market_abuse.py`)
규칙은 재구현하지 않고 호출만 한다: CM-9는 CM-3 `evaluator.evaluate_bundle`
(기존 fail-closed 래퍼 + 최악판정 채택)에 얹고, CM-10은 `market_abuse.
detect()`를 그대로 부른다 -- 이 파일에 crossing-price/wash-window/
spoofing-ratio 수식이 다시 나오면 결함이다.

위반(`ComplianceVerdict.DENY`)은 `KillSwitchService.activate()`(TENANT
범위)로 이어진다 -- R-40/I3(safety_control insert 호출부는 정확히 한 곳,
`postgres_repository.py`) 때문에 여기서 그 테이블에 직접 쓰지 않는다. 그
결과 tenant의 다음 주문은 이미 배선된 `foundation_gate.py`의 ACTIVE
control 검사에 걸려 거부된다 -- 새 게이트 없이 기존 경로에 올라탄다.

스코프 축소(미검증, 후속 리프 대상): `position_qty`는 `positions`의 현재
수량 스냅샷(체결별 running balance 아님). `borrow_available_qty`는 항상
0 -- LA-25 `pos_borrow_position`(`positions/domain/borrow.py`)엔 아직
조회 어댑터가 없어 "락 없음"을 안전측 기본값으로 쓴다. `market_close_at`
은 실제 거래소 마감이 아니라 UTC 자정(다음날 0시)이다.

멱등키(DoD (c))는 `safety_control.idempotency_digest`(UNIQUE, 이미
`f4b9d6e5a7c8`가 만들었지만 아직 어떤 호출부도 채우지 않음) 대신, activate
전에 같은 reason(tenant+rule_code+business_date)의 ACTIVE control이 있는지
조회해 건너뛰는 방식을 쓴다 -- 그 컬럼을 쓰려면 `insert_safety_control()`
시그니처를 바꿔야 해 선언된 파일 범위 밖이다. read-then-write라 동시
레이스엔 안전하지 않지만, 이 배치는 단일 직렬 tick으로만 돈다(안 겹침).
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

# watchdog_process.WATCHDOG_SYSTEM_ACTOR_ID / e5a8c5d4f6b7가 심은 시스템
# `users` 행 -- liquidation_executor.py와 동일한 관행으로 로컬 재정의.
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
    return RuleBundle(
        version=_CM9_BUNDLE_VERSION,
        rules=(
            RuleSpec(
                rule_id=short_sale.RULE_ID,
                params=rule_params.get("short_sale", {}),
                check=short_sale.check,
            ),
            RuleSpec(
                rule_id=wash_trade.RULE_ID,
                params=rule_params.get("wash_trade", {}),
                check=wash_trade.check,
            ),
        ),
    )


def evaluate_tenant_day(
    *,
    fill_snapshots: Sequence[Mapping[str, Any]],
    market_abuse_window: Mapping[str, Any],
    rule_params: Mapping[str, Mapping[str, Any]],
    now: datetime,
) -> tuple[list[PostTradeViolation], list[PostTradeViolation]]:
    """순수 평가(한 tenant의 하루), I/O·시계 읽기 없음(`now`는 `evaluate_
    bundle`과 같은 규약으로 호출자 주입). CM-9 두 규칙은 히트하면 항상
    DENY, CM-10 실제 탐지 3종은 WARN(`DATA_MISSING`만 DENY, I-02) -- §3
    "WARN은 통과시키되 기록"에 따라 `warnings`로만 나가고 차단하지 않는다.
    규칙/패턴 id로 중복 제거(체결 여러 건이 같은 규칙을 여러 번 히트할 수
    있음)."""
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
    except Exception:  # noqa: BLE001 -- DoD (d) fail-closed: 예외는 판정
        # 없음이 아니라 차단으로 이어져야 한다.
        abuse_hits = [
            AbuseHit(
                pattern_id="market_abuse.evaluation_exception",
                severity=ComplianceVerdict.DENY,
                reason_code="EVALUATION_EXCEPTION",
                message="market_abuse.detect() raised -- fail-closed block",
                evidence={},
            )
        ]
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
    """CM-9 체결 스냅샷 + CM-10 윈도우를 `fills`/`orders`/`positions`에서
    조립(스코프 축소는 모듈 docstring 참조)."""
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


async def _has_active_control(repo: RiskGateRepository, tenant_id: UUID, reason: str) -> bool:
    controls = await repo.list_active_controls(tenant_id=tenant_id)
    return any(c.scope == SafetyScope.TENANT and c.reason == reason for c in controls)


async def run_daily_post_trade_batch(
    pool: asyncpg.Pool,
    kill_switch: KillSwitchService,
    risk_gate_repo: RiskGateRepository,
    *,
    business_date: date,
    now: datetime,
    rule_params: Mapping[str, Mapping[str, Any]] | None = None,
) -> PostTradeBatchReport:
    """§9 CM-11 공개 진입점 -- `background_loops.py`가 주기적으로 호출한다."""
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
            fill_snapshots=fill_snapshots,
            market_abuse_window=market_abuse_window,
            rule_params=resolved_params,
            now=now,
        )
        if tenant_warnings:
            warnings[tenant_id] = tuple(v.rule_code for v in tenant_warnings)
        if not blocking:
            continue

        activated: list[str] = []
        for violation in blocking:
            reason = _violation_reason(violation.rule_code, business_date)
            if await _has_active_control(risk_gate_repo, tenant_id, reason):
                activated.append(violation.rule_code)
                continue
            await kill_switch.activate(
                scope=SafetyScope.TENANT,
                scope_ref=str(tenant_id),
                reason=reason,
                actor_subject_id=SYSTEM_ACTOR_ID,
                actor_is_admin=True,
                trace_id=uuid4(),
            )
            logger.warning(
                "evaluate_post_trade: tenant=%s rule=%s business_date=%s -- COMPLIANCE 차단",
                tenant_id,
                violation.rule_code,
                business_date,
            )
            activated.append(violation.rule_code)
        blocked[tenant_id] = tuple(activated)

    return PostTradeBatchReport(
        business_date=business_date,
        tenants_evaluated=len(tenant_ids),
        blocked=blocked,
        warnings=warnings,
    )
