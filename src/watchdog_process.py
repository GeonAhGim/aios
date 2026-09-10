"""9.1 — Watchdog 프로세스 골격 (별도 프로세스, heartbeat 파일로만 통신).

Spec: 기능설계문서_v1.20.md#FD-9.1/FD-9.2, 정책문서 8.6-A

정책문서 8.6-A "메인과 완전 격리된 독립 헬스체크 프로세스" 원칙 —
`python -m src.watchdog_process`로 main.py(uvicorn)과 별도 OS 프로세스로
띄운다. 메인 프로세스와 메모리를 전혀 공유하지 않는다(공유하는 건
core/safety/heartbeat.py의 파일 타임스탬프와 Postgres뿐 — main.py의
InProcessEventBus/app.state는 다른 OS 프로세스인 이 스크립트에서 애초에
접근할 방법이 없다).

편차(정직한 축소, 사용자 승인 2026-09-02로 부분 해소) — FD-9.1의 loss_pct
계산(compute_equity)은 실제 주문 체결 파이프라인이 필요했으나, execution_loop
이 실제로 돌기 시작한 것을 근거로 항상 0을 반환하던 스텁을 걷어냈다. 지금은
RUNNING 실행들의 (allocated_capital + realized_pnl 합)을 시스템 전체 근사
equity로 쓴다 — 거래소 자격증명 없이 DB만으로 계산 가능해 "메인 프로세스와
완전 격리" 원칙을 해치지 않는다.

남은 근사 한계(정직한 축소): (1) positions.unrealized_pnl은 mark-to-market
갱신 경로가 없어 항상 0(포지션을 닫아야만 realized_pnl로 잡힌다). (2)
다중테넌시 — 모든 사용자의 RUNNING 실행을 시스템 전체 숫자 하나로 합산한다.
exchange_healthy는 Bitget 공개 시세 API(서명 검증 없음, 빈 문자열 키로도
실호출 성공 확인됨)를 호출한다 — 계정과 무관한 인프라 신호라 다중테넌시
문제 자체가 없다.

9.3 Split-Brain 진단(core/safety/split_brain.py, 이번에 처음 실배선) — 매
사이클 DB 연결도 별도로 확인해 "DB만 단독 장애"인지 구분한다.
DB_ISOLATED_FAILURE로 진단되면 강제조치를 하지 않는다(어차피 DB가 끊겼다는
전제로만 의미 있는 조치라 진단 결과는 logger로만 남긴다).

HALT/LIQUIDATE 판정 적용(Split-Brain이 DB 단독장애가 아니라고 판단했을 때만)
— R-51(task-2357)부터 `KillSwitchService.activate`(scope=GLOBAL)에 위임한다:
RUNNING 실행 paused_by='SAFETY_LAYER' 전환 + paper_control fan-out +
open_order_sweeper가 모두 그 안에서 일어난다(§4.3 412행). watchdog은 이
전이를 더 이상 직접 재구현하지 않는다(I3 — `INSERT INTO safety_control`
호출부는 `postgres_repository.py` 한 곳뿐이어야 한다). `exchange_adapters={}`
로 넘긴다 — credential_resolver를 의도적으로 배선하지 않으므로
open_order_sweeper의 실제 거래소 취소 호출은 전부 `adapter_failed`로
남는다(로컬 DB 전이는 일어남, DoD(h) — 이 리프는 주문을 보내지 않는다).

watchdog.decision.triggered 알림은 이 프로세스가 직접 발행하지 않는다(
InProcessEventBus는 프로세스 경계를 못 넘는다) — audit_log 기록(+
KillSwitchService의 audit_event) 자체가 사실의 원천이고, 메인 프로세스가
그 사실을 감지해 재발행하는 건 별도 leaf(아웃박스 폴러) 대상이다.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg

from src.core.loader.secret_loader import load_env_secrets
from src.core.logging.audit_log import record_audit_log
from src.core.safety.heartbeat import DEFAULT_HEARTBEAT_PATH
from src.core.safety.split_brain import CheckFn, Diagnosis, SplitBrainDiagnostics
from src.core.safety.watchdog import (
    DEFAULT_UNRESPONSIVE_SEC_THRESHOLD,
    WatchdogAction,
    WatchdogDecision,
    WatchdogService,
    decide,
)
from src.exchanges.bitget.adapter import BitgetAdapter
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.paper_control.adapters.postgres_repository import PostgresPaperControlRepository
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.safety.kill_switch_service import KillSwitchService

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5.0  # Draft — FD-9.1 원문 주기

# e5a8c5d4f6b7_liquidation_request.py가 심는 시스템 액터 행과 같은 값이어야
# 한다 — GLOBAL activate()는 실제 users FK를 요구하는데, 이 판정은 특정
# tenant 하나가 아니라 시스템 전체 근거라 빌려올 tenant가 없다(마이그레이션
# docstring 참조).
WATCHDOG_SYSTEM_ACTOR_ID = UUID("00000000-0000-0000-0000-000000000002")


def _asyncpg_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://")


def build_kill_switch_service(pool: asyncpg.Pool) -> KillSwitchService:
    return KillSwitchService(
        risk_gate_repo=PostgresRiskGateRepository(pool),
        pg_pool=pool,
        paper_control_repo=PostgresPaperControlRepository(pool),
        exchange_adapters={},
        audit_repo=PostgresAuditEventRepository(pool),
    )


class _LastAppliedAction:
    """같은 판정이 사이클(5초)마다 반복돼도 매번 새 control(fence++)을 만들지
    않도록 막는 프로세스 내 상태. NORMAL로 돌아오면 리셋된다(activate()는
    호출마다 항상 성공하는 게 설계 의도라 dedup은 이 프로세스의 책임)."""

    def __init__(self) -> None:
        self.value: WatchdogAction = WatchdogAction.NORMAL


async def _apply_decision(
    pool: asyncpg.Pool, decision: WatchdogDecision, kill_switch: KillSwitchService
) -> None:
    """control 생성은 전부 `KillSwitchService.activate`에 위임한다(DoD(f)).
    LIQUIDATE만 그 결과(control id·fence_token)로 `liquidation_request`를
    REQUESTED로 INSERT한다(§4 426~430행) — activate()가 자기 트랜잭션을 커밋한
    뒤 반환하므로(§5 "트랜잭션 경계", 커넥션을 쥔 채 감싸면 P1 교착) 두
    INSERT는 서로 다른 트랜잭션이다(그 사이 크래시는 알려진 잔여 위험). 이
    INSERT 실패는 fan-out 실패와 달리 판정의 핵심 결과라 삼키지 않는다."""
    view = await kill_switch.activate(
        scope=SafetyScope.GLOBAL,
        scope_ref=None,
        reason=decision.reason,
        actor_subject_id=WATCHDOG_SYSTEM_ACTOR_ID,
        actor_is_admin=True,
        trace_id=uuid4(),
    )

    liquidation_request_id: UUID | None = None
    if decision.action == WatchdogAction.LIQUIDATE:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO liquidation_request "
                "(safety_control_id, scope, scope_ref, state, requested_by, fence_token) "
                "VALUES ($1, 'GLOBAL', '', 'REQUESTED', 'watchdog_process', $2) "
                "RETURNING id",
                view.id,
                view.fence_token,
            )
        liquidation_request_id = row["id"]

    request_id_str = str(liquidation_request_id) if liquidation_request_id else None
    async with pool.acquire() as conn, conn.transaction():
        await record_audit_log(
            conn,
            actor_agent="watchdog_process",
            action_type="watchdog.decision.applied",
            decision_data={
                "action": decision.action.value,
                "reason": decision.reason,
                "control_id": str(view.id),
                "fence_token": view.fence_token,
                "liquidation_request_id": request_id_str,
            },
            target_type="system",
            target_id="all_running_executions",
        )
    logger.critical(
        "Watchdog %s 발동: %s (control=%s, fence=%s, liquidation_request=%s)",
        decision.action.value,
        decision.reason,
        view.id,
        view.fence_token,
        liquidation_request_id,
    )


class _LatestExchangeHealth:
    """레드팀 감사(#06) 반영 — 사이클당 check_exchange()를 정확히 한 번만
    호출하고, take_snapshot()의 health_check와 split_brain.diagnose()가 같은
    사이클 안에서 그 결과를 재사용하기 위한 캐시(이전엔 사이클당 2회 중복
    호출됐다)."""

    def __init__(self) -> None:
        self.value = False

    async def get(self) -> bool:
        return self.value


async def run_one_cycle(
    pool: asyncpg.Pool,
    service: WatchdogService,
    split_brain: SplitBrainDiagnostics,
    *,
    check_exchange: CheckFn,
    check_db: CheckFn,
    exchange_health_cache: _LatestExchangeHealth,
    kill_switch: KillSwitchService,
    last_action: _LastAppliedAction,
) -> None:
    """한 사이클(거래소 헬스체크→스냅샷→Split-Brain 진단→판정→조건부 조치) —
    run_forever의 루프 몸체를 분리한 것(테스트 가능하도록, 순수 리팩터링).
    exchange_healthy는 decide()의 판정 입력이 아니다(HALT/LIQUIDATE/NORMAL은
    loss_pct·unresponsive_sec만 본다) — 거래소 응답성 판정은 Split-Brain이
    전담한다. RTF-03(2721): failure_domain 실전달, market_wide_correlated는 여전히 None."""
    exchange_health_cache.value = await check_exchange()
    snapshot = await service.take_snapshot()
    failure_domain = await split_brain.diagnose(
        check_exchange=exchange_health_cache.get,
        check_db=check_db,
        main_process_ok_raw=snapshot.unresponsive_sec < DEFAULT_UNRESPONSIVE_SEC_THRESHOLD,
    )
    decision = decide(snapshot, market_wide_correlated=None, failure_domain=failure_domain)
    logger.info(
        "Watchdog snapshot=%s decision=%s failure_domain=%s", snapshot, decision, failure_domain
    )

    if failure_domain.diagnosis == Diagnosis.DB_ISOLATED_FAILURE:
        # FD-9.3 원문 — DB만 단독 장애면 강제청산 대상에서 제외하고 신규주문만
        # 보류한다(그 이상의 강제조치는 하지 않는다).
        logger.warning(
            "Split-Brain: DB 단독 장애로 진단 — Watchdog 강제조치 보류 "
            "(신규주문만 자연히 막힘, 강제청산 미실행)"
        )
        # 조치를 적용 안 했으니 리셋 — 안 그러면 DB 단독 장애가 풀린 뒤 같은
        # action이 "이미 적용됨"으로 오인돼 진짜 판정이 조용히 스킵된다.
        last_action.value = WatchdogAction.NORMAL
    elif decision.action != WatchdogAction.NORMAL and decision.action != last_action.value:
        await _apply_decision(pool, decision, kill_switch)
        last_action.value = decision.action
    else:
        last_action.value = decision.action


async def compute_system_equity(pool: asyncpg.Pool) -> Decimal:
    """모듈 docstring 편차 설명 참조 — RUNNING 실행들의 (allocated_capital +
    종가 실현손익 합)을 시스템 전체 근사 equity로 쓴다(LEFT JOIN 뒤 실행당
    1행으로 묶어 allocated_capital 중복 합산을 피한다)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT e.allocated_capital, COALESCE(SUM(p.realized_pnl), 0) AS realized_pnl
            FROM strategy_executions e
            LEFT JOIN positions p ON p.execution_id = e.id
            WHERE e.status = 'RUNNING'
            GROUP BY e.id
            """
        )
    return sum((row["allocated_capital"] + row["realized_pnl"] for row in rows), Decimal("0"))


async def run_forever(pool: asyncpg.Pool) -> None:
    exchange_probe = BitgetAdapter("", "", "", demo_mode=True)

    async def compute_equity() -> Decimal:
        return await compute_system_equity(pool)

    async def check_exchange() -> bool:
        try:
            await exchange_probe.get_ticker("BTC/USDT")
        except Exception:  # noqa: BLE001 — 응답 없음=장애 의심, 낙관적으로 True 취급 안 함
            return False
        return True

    async def check_db() -> bool:
        try:
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
        except Exception:  # noqa: BLE001
            return False
        return True

    exchange_health_cache = _LatestExchangeHealth()

    service = WatchdogService(
        compute_equity=compute_equity,
        health_check=exchange_health_cache.get,
        heartbeat_path=DEFAULT_HEARTBEAT_PATH,
    )
    split_brain = SplitBrainDiagnostics()
    kill_switch = build_kill_switch_service(pool)
    last_action = _LastAppliedAction()

    try:
        while True:
            await run_one_cycle(
                pool,
                service,
                split_brain,
                check_exchange=check_exchange,
                check_db=check_db,
                exchange_health_cache=exchange_health_cache,
                kill_switch=kill_switch,
                last_action=last_action,
            )
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    finally:
        await exchange_probe.aclose()


async def main() -> None:
    secrets = load_env_secrets()
    pool = await asyncpg.create_pool(_asyncpg_dsn(secrets.database_url.get_secret_value()))
    try:
        await run_forever(pool)
    finally:
        await pool.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
