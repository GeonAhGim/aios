"""9.1 — Watchdog 프로세스 골격 (별도 프로세스, heartbeat 파일로만 통신).

Spec: 기능설계문서_v1.20.md#FD-9.1/FD-9.2, 정책문서 8.6-A

정책문서 8.6-A "메인과 완전 격리된 독립 헬스체크 프로세스" 원칙 —
`python -m src.watchdog_process`로 main.py(uvicorn)과 별도 OS 프로세스로
띄운다. FD-9.1 완료조건("메인 프로세스를 강제로 정지시켰을 때 Watchdog가
계속 동작하며 unresponsive_sec 증가를 관측")이 요구하는 격리 수준이라
메인 프로세스와 메모리를 전혀 공유하지 않는다 — 공유하는 건
core/safety/heartbeat.py의 파일 타임스탬프와 Postgres뿐이다. main.py의
InProcessEventBus/app.state는 다른 OS 프로세스인 이 스크립트에서 애초에
접근할 방법이 없다(같은 컴퓨터라도 별도 프로세스는 별도 메모리 공간).

편차(정직한 축소) — FD-9.1의 loss_pct 계산(compute_equity)은 사용자별
실제 계좌 자본곡선이 있어야 의미가 생기는데, 그러려면 사용자별 거래소
자격증명 조회 + 실제 주문 체결 파이프라인(FD-4/FD-8, FROZEN Zone)이
필요하다 — 이 세션엔 그 실행 엔진 자체가 없다(strategy_executions는
행만 만들 뿐 실제로 매매하는 루프가 없다, execution_service.py 자체
docstring 참조). 그래서 compute_equity는 항상 같은 값을 반환해
loss_pct=0으로 고정한다 — 데이터가 없다고 손실을 지어내지 않는다는
판단이다. 결과적으로 지금 실제로 동작하는 건 FD-9.1
unresponsive_sec 감시 → FD-9.2 HALT 판정 경로뿐이고, loss_pct 기반
LIQUIDATE 경로는 FD-4/8이 생기기 전까지 사실상 죽어있다 — 버그가
아니라 이 세션이 실제로 만들 수 있는 것을 정직하게 반영한 상태다.

exchange_healthy(FD-9.1 원문: "거래소 API 자체의 독립 응답성")는 실제로
Bitget 공개 시세 API(GET /api/v2/spot/market/tickers)를 호출해 확인한다
— 이 엔드포인트는 서명 검증을 하지 않아(직접 확인함, 빈 문자열 키로도
실호출 성공) 사용자별 자격증명 없이도 "거래소 자체가 응답하는가"를
계정과 무관하게 진짜로 물어볼 수 있다. 다중테넌시라 "감시할 특정 계좌"는
여전히 하나로 정해지지 않지만, 이 신호는 계좌와 무관한 인프라 신호라
그 문제 자체가 없다.

9.3 Split-Brain 진단(core/safety/split_brain.py, 이미 구현+단위테스트
완료 — 이번에 처음 실배선) — 매 사이클 DB 연결도 별도로 확인해 "DB만
단독 장애"인지 "거래소/메인프로세스까지 문제"인지 구분한다.
DB_ISOLATED_FAILURE로 진단되면 FD-9.3 원문대로 강제조치(_apply_decision,
DB에 UPDATE를 시도하는 행위 자체)를 하지 않는다 — 어차피 DB가 안
끊겼다는 전제로만 의미 있는 조치이고, 실제로 DB가 죽었으면 그 UPDATE도
실패할 뿐이다. 이 진단 결과는 DB에 못 쓰니(감사기록조차 불가능한
상황) logger로만 남긴다.

HALT/LIQUIDATE 판정 시 실제로 적용하는 조치(Split-Brain이 DB 단독장애가
아니라고 판단했을 때만): RUNNING인 모든 실행을 paused_by='SAFETY_LAYER'
로 전환한다 — FD-16.3(execution_service.py::start())이 이미 이 값을
존중해 사용자가 직접 재시작할 수 없도록 구현돼 있으므로, 이 값을
바꾸는 것만으로 실제 강제효과가 생긴다. watchdog.decision.triggered
알림은 이 프로세스가 직접 발행하지 않는다 — InProcessEventBus는 프로세스
경계를 못 넘는다(core/event_bus/in_process.py 자체 docstring: "단일
프로세스 내에서만 동작"). 대신 audit_log 기록 + strategy_executions.
paused_by 변경 자체를 사실의 원천으로 남겨두고, 메인 프로세스가 그
사실을 감지해 이벤트로 재발행하는 건 별도 leaf(아웃박스 폴러) 대상이다.
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

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

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5.0  # Draft — FD-9.1 원문 주기


def _asyncpg_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://")


async def _apply_decision(pool: asyncpg.Pool, decision: WatchdogDecision) -> None:
    async with pool.acquire() as conn, conn.transaction():
        result = await conn.execute(
            "UPDATE strategy_executions SET status = 'PAUSED', paused_by = 'SAFETY_LAYER' "
            "WHERE status = 'RUNNING'"
        )
        await record_audit_log(
            conn,
            actor_agent="watchdog_process",
            action_type="watchdog.decision.applied",
            decision_data={
                "action": decision.action.value,
                "reason": decision.reason,
                "db_result": result,
            },
            target_type="system",
            target_id="all_running_executions",
        )
    logger.critical(
        "Watchdog %s 발동: %s (%s)", decision.action.value, decision.reason, result
    )


class _LatestExchangeHealth:
    """레드팀 감사(docs/RED_TEAM_FINDINGS.md #06) 반영 — 사이클당
    check_exchange()를 정확히 한 번만 실제로 호출하고, 그 결과를
    WatchdogService.take_snapshot()(생성자에 바인딩된 health_check)와
    split_brain.diagnose()가 같은 사이클 안에서 재사용하기 위한 캐시.
    이전에는 두 곳이 각각 실제 Bitget 공개 API를 호출해 사이클당 2회
    중복 호출됐다."""

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
) -> None:
    """한 사이클(거래소 헬스체크→스냅샷→판정→Split-Brain 진단→조건부 조치)
    — run_forever의 루프 몸체를 그대로 분리한 것. 무한루프 안에 인라인돼
    있으면 테스트가 불가능해서 뽑아냈다(동작은 동일, 순수 리팩터링).

    exchange_healthy는 FD-9.2 decide()의 판정 입력이 아니다(HALT/
    LIQUIDATE/NORMAL 판정은 loss_pct·unresponsive_sec만 본다 — 기능
    설계문서 FD-9.2 처리단계 원문 그대로) — 거래소 자체 응답성 판정은
    FD-9.3 Split-Brain이 전담하는 것이 원래 설계다. WatchdogSnapshot에
    담기는 건 사람이 로그로 확인할 수 있게 남기는 관측치일 뿐이다."""
    exchange_health_cache.value = await check_exchange()

    snapshot = await service.take_snapshot()
    decision = decide(snapshot, market_wide_correlated=None)

    failure_domain = await split_brain.diagnose(
        check_exchange=exchange_health_cache.get,
        check_db=check_db,
        main_process_ok_raw=snapshot.unresponsive_sec < DEFAULT_UNRESPONSIVE_SEC_THRESHOLD,
    )
    logger.info(
        "Watchdog snapshot=%s decision=%s failure_domain=%s", snapshot, decision, failure_domain
    )

    if failure_domain.diagnosis == Diagnosis.DB_ISOLATED_FAILURE:
        # FD-9.3 원문 — DB만 단독 장애면 강제청산 대상에서 제외하고 신규주문만
        # 보류한다. 여기서는 그 이상의 행동(DB에 UPDATE 시도)을 아예 하지
        # 않는 것으로 구현한다 — 어차피 DB가 끊겼다는 진단이라 그 UPDATE
        # 자체가 성립하지 않는다.
        logger.warning(
            "Split-Brain: DB 단독 장애로 진단 — Watchdog 강제조치 보류 "
            "(신규주문만 자연히 막힘, 강제청산 미실행)"
        )
    elif decision.action != WatchdogAction.NORMAL:
        await _apply_decision(pool, decision)


async def run_forever(pool: asyncpg.Pool) -> None:
    exchange_probe = BitgetAdapter("", "", "", demo_mode=True)

    async def compute_equity() -> Decimal:
        return Decimal("0")  # 모듈 docstring 편차 설명 참조 — 항상 loss_pct=0

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

    try:
        while True:
            await run_one_cycle(
                pool,
                service,
                split_brain,
                check_exchange=check_exchange,
                check_db=check_db,
                exchange_health_cache=exchange_health_cache,
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
