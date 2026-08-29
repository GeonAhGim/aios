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
health_check도 같은 이유로 "거래소 API 응답성"(FD-9.1 원문) 대신
Postgres 연결 확인으로 대체한다 — 감시할 특정 거래소 계좌가 다중테넌시
구조상 하나로 정해지지 않기 때문(별도 leaf에서 사용자별 루프로
재설계 필요, 지금은 exchange_healthy를 인프라 헬스로 좁혀 쓴다).

HALT/LIQUIDATE 판정 시 실제로 적용하는 조치: RUNNING인 모든 실행을
paused_by='SAFETY_LAYER'로 전환한다 — FD-16.3(execution_service.py::
start())이 이미 이 값을 존중해 사용자가 직접 재시작할 수 없도록
구현돼 있으므로, 이 값을 바꾸는 것만으로 실제 강제효과가 생긴다.
watchdog.decision.triggered 알림은 이 프로세스가 직접 발행하지 않는다
— InProcessEventBus는 프로세스 경계를 못 넘는다(core/event_bus/
in_process.py 자체 docstring: "단일 프로세스 내에서만 동작"). 대신
audit_log 기록 + strategy_executions.paused_by 변경 자체를 사실의
원천으로 남겨두고, 메인 프로세스가 그 사실을 감지해 이벤트로
재발행하는 건 별도 leaf(아웃박스 폴러) 대상이다.
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

import asyncpg

from src.core.loader.secret_loader import load_env_secrets
from src.core.logging.audit_log import record_audit_log
from src.core.safety.heartbeat import DEFAULT_HEARTBEAT_PATH
from src.core.safety.watchdog import WatchdogAction, WatchdogDecision, WatchdogService, decide

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


async def run_forever(pool: asyncpg.Pool) -> None:
    async def compute_equity() -> Decimal:
        return Decimal("0")  # 모듈 docstring 편차 설명 참조 — 항상 loss_pct=0

    async def health_check() -> bool:
        try:
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
        except Exception:  # noqa: BLE001 — DB 접근 실패도 "응답 없음"으로 취급
            return False
        return True

    service = WatchdogService(
        compute_equity=compute_equity,
        health_check=health_check,
        heartbeat_path=DEFAULT_HEARTBEAT_PATH,
    )
    while True:
        snapshot = await service.take_snapshot()
        decision = decide(snapshot, market_wide_correlated=None)
        logger.info("Watchdog snapshot=%s decision=%s", snapshot, decision)
        if decision.action != WatchdogAction.NORMAL:
            await _apply_decision(pool, decision)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def main() -> None:
    secrets = load_env_secrets()
    pool = await asyncpg.create_pool(_asyncpg_dsn(secrets.database_url))
    try:
        await run_forever(pool)
    finally:
        await pool.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
