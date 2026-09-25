"""U-15 PERSONAL mode API — solo-operator risk bundle status, PAPER->LIVE
promotion checklist, dashboard kill button, daily report.

Spec: task-2749, ADR-2026-09-09-B Decision C extension (solo-operator
branch). Per rule 71 §6, routers only handle auth/injection/transport
validation/command invocation — domain exceptions (`PromotionDeniedError`)
are not caught here; `EXCEPTION_MAP` in
`src/api/contracts/exception_mapping.py` translates them to
`POLICY_LIVE_BLOCKED` (403) in the global handler.

This is a single-operator tool, so every endpoint requires
`get_current_admin` (operator only) — it is not a general-user
self-service path.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query

from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import get_current_admin
from src.foundation.risk.adapters.bundle_loader import load_personal_bundle
from src.foundation.risk.adapters.json_state_store import JsonPersonalStateStore
from src.foundation.risk.adapters.telegram_adapter import TelegramNotifierAdapter
from src.foundation.risk.application.daily_report import build_daily_report
from src.foundation.risk.application.promotion_checklist import (
    PersonalPromotionChecklist,
    build_promotion_checklist,
    request_promotion,
)
from src.foundation.risk.contracts.v1 import (
    DailyReportView,
    KillSwitchView,
    PersonalRiskBundleView,
    PromotionChecklistView,
)
from src.foundation.risk.domain.models import PERSONAL_CONSERVATIVE_BUNDLE_NAME
from src.foundation.risk.domain.promotion import MIN_PAPER_DAYS
from src.foundation.risk.ports.notifier import PersonalNotification, PersonalNotificationKind
from src.foundation.risk.ports.state import PersonalOperationStatePort
from src.services.auth_service import User

router = APIRouter(prefix="/v1/foundation/personal", tags=["foundation:personal"])


def get_personal_state() -> PersonalOperationStatePort:
    return JsonPersonalStateStore()


def get_personal_notifier() -> TelegramNotifierAdapter:
    return TelegramNotifierAdapter()


def _checklist_view(checklist: PersonalPromotionChecklist) -> PromotionChecklistView:
    return PromotionChecklistView(
        eligible=checklist.eligible,
        blockers=[b.value for b in checklist.blockers],
        min_paper_days=MIN_PAPER_DAYS,
        paper_days_elapsed=checklist.paper_days_elapsed,
        paper_violation_count=checklist.paper_violation_count,
    )


@router.get("/bundle")
async def get_bundle(
    _admin: User = Depends(get_current_admin),
) -> ApiResponse[PersonalRiskBundleView]:
    bundle = load_personal_bundle()
    return ok(
        PersonalRiskBundleView(
            name=bundle.name,
            position_pct_of_equity=float(bundle.position_pct_of_equity),
            daily_loss_kill_pct=float(bundle.daily_loss_kill_pct),
            max_exposure_pct=float(bundle.max_exposure_pct),
            default_notional_cap_krw=float(bundle.default_notional_cap_krw),
            symbol_whitelist=sorted(bundle.symbol_whitelist),
        )
    )


@router.get("/promotion-checklist")
async def get_promotion_checklist(
    _admin: User = Depends(get_current_admin),
    state: PersonalOperationStatePort = Depends(get_personal_state),
) -> ApiResponse[PromotionChecklistView]:
    bundle = load_personal_bundle()
    checklist = await build_promotion_checklist(
        bundle_active=bundle.name == PERSONAL_CONSERVATIVE_BUNDLE_NAME, state=state
    )
    return ok(_checklist_view(checklist))


@router.post("/promote")
async def post_promote(
    _admin: User = Depends(get_current_admin),
    state: PersonalOperationStatePort = Depends(get_personal_state),
) -> ApiResponse[PromotionChecklistView]:
    """If the checklist is unmet, `PromotionDeniedError` propagates and the
    global handler denies with 403 `POLICY_LIVE_BLOCKED` (fail-closed) —
    a normal return from this function means the checklist is fully
    satisfied."""
    bundle = load_personal_bundle()
    checklist = await request_promotion(
        bundle_active=bundle.name == PERSONAL_CONSERVATIVE_BUNDLE_NAME, state=state
    )
    return ok(_checklist_view(checklist))


@router.get("/kill")
async def get_kill_switch(
    _admin: User = Depends(get_current_admin),
    state: PersonalOperationStatePort = Depends(get_personal_state),
) -> ApiResponse[KillSwitchView]:
    return ok(
        KillSwitchView(engaged=await state.is_kill_engaged(), reason=await state.kill_reason())
    )


@router.post("/kill")
async def post_kill_switch(
    _admin: User = Depends(get_current_admin),
    state: PersonalOperationStatePort = Depends(get_personal_state),
    notifier: TelegramNotifierAdapter = Depends(get_personal_notifier),
) -> ApiResponse[KillSwitchView]:
    """One-click dashboard kill button."""
    await state.engage_kill(reason="MANUAL_DASHBOARD_KILL")
    await notifier.send(
        PersonalNotification(
            kind=PersonalNotificationKind.KILL_SWITCH,
            message="Operator manually engaged the kill switch from the dashboard",
        )
    )
    return ok(
        KillSwitchView(engaged=await state.is_kill_engaged(), reason=await state.kill_reason())
    )


@router.get("/daily-report")
async def get_daily_report(
    _admin: User = Depends(get_current_admin),
    state: PersonalOperationStatePort = Depends(get_personal_state),
    report_date: date | None = Query(default=None),
    realized_pnl_krw: Decimal = Query(default=Decimal("0")),
    fill_count: int = Query(default=0, ge=0),
) -> ApiResponse[DailyReportView]:
    """Unverified/TODO: fill/P&L source wiring is a separate leaf — for now
    the caller passes today's P&L and fill count directly as query
    parameters."""
    effective_date = report_date if report_date is not None else datetime.now(timezone.utc).date()
    report = await build_daily_report(
        report_date=effective_date,
        realized_pnl_krw=realized_pnl_krw,
        fill_count=fill_count,
        state=state,
    )
    return ok(
        DailyReportView(
            report_date=report.report_date.isoformat(),
            realized_pnl_krw=float(report.realized_pnl_krw),
            fill_count=report.fill_count,
            violation_count=report.violation_count,
        )
    )
