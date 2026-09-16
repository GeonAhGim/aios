"""U-15 PERSONAL 모드 API — 개인 운영자용 리스크 번들 상태·PAPER→LIVE 승격
체크리스트·대시보드 kill 버튼·일일 리포트.

Spec: task-2749, ADR-2026-09-09-B Decision C 확장(사용자 개인 운영 곁가지).
71번 §6 규칙에 따라 router는 auth/주입/transport validation/command
invocation만 담당한다 — 도메인 예외(`PromotionDeniedError`)는 여기서 잡지
않고 `src/api/contracts/exception_mapping.py`의 `EXCEPTION_MAP`이 전역
핸들러에서 `POLICY_LIVE_BLOCKED`(403)로 번역한다.

단일 운영자 도구이므로 모든 엔드포인트는 `get_current_admin`(운영자) 전용
이다 — 일반 사용자 self-service 경로가 아니다.
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
    """체크리스트 미충족이면 `PromotionDeniedError`가 그대로 전파되어 전역
    핸들러가 403 `POLICY_LIVE_BLOCKED`로 거부한다(fail-closed) — 이 함수가
    정상 반환하면 승격 체크리스트가 전부 충족됐다는 뜻이다."""
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
    """대시보드 원클릭 kill 버튼."""
    await state.engage_kill(reason="MANUAL_DASHBOARD_KILL")
    await notifier.send(
        PersonalNotification(
            kind=PersonalNotificationKind.KILL_SWITCH,
            message="운영자가 대시보드에서 수동으로 kill 스위치를 발동함",
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
    """미검증/TODO: 체결·손익 소스 연동은 별도 리프 — 현재는 호출자가
    query parameter로 그날의 손익·체결 건수를 직접 넘긴다."""
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
