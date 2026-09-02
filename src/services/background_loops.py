"""16번대 — main.py lifespan의 백그라운드 루프(heartbeat/alert/risk_guard/
execution_loop/safety) 생성·복구·취소.

Spec: 16_backend_signatures.md, ADR-2026-08-10-B, P6(파일당 300줄 초과 금지)

편차: task-117은 원래 이 모듈을 src/app/background_loops.py에 두려 했지만,
.aios-zone(Meta-Control Plane, 사람만 수정)이 `src/app/**`를 선언하지 않아
새 zone 없이는 만들 수 없다 — zone 정책 파일 수정은 에이전트 금지(P8)이므로
이미 SCAFFOLD로 선언된 `src/services/**` 아래로 대신 둔다.

main.py는 pool/event_bus/credential_resolver 등 앱 전역 객체를 조립한 뒤 이
모듈의 :func:`start_background_loops`에 넘겨 루프를 띄우고, shutdown 시
반환된 :class:`BackgroundLoops`의 :meth:`~BackgroundLoops.stop`만 호출한다.
동작은 분리 이전과 동일 — 이 모듈은 main.py에 있던 코드를 그대로 옮긴 것이다.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from dataclasses import dataclass, field

import asyncpg

from src.core.event_bus.bus import EventBus
from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.safety.circuit_breaker import CircuitBreakerService
from src.core.safety.heartbeat import DEFAULT_HEARTBEAT_PATH, write_heartbeat
from src.core.safety.metrics_collector import ApiCallTracker, collect_circuit_breaker_metrics
from src.services.alert_service import AlertService
from src.services.credential_resolver import CredentialResolver
from src.services.execution_loop.recovery_wiring import recover_orders_on_startup
from src.services.execution_loop.scheduler import ExecutionLoopScheduler
from src.services.execution_service import ExecutionService
from src.services.risk_guard_service import RiskGuardService

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 2.0  # Draft — watchdog_process.py의 5초 폴링 주기보다 짧게
ALERT_EVALUATION_INTERVAL_SECONDS = 60.0  # Draft — 가격/지표 알림 평가 주기
RISK_GUARD_INTERVAL_SECONDS = 30.0  # Draft — 손실 한도 자동정지 평가 주기
SAFETY_REACTIVATION_INTERVAL_SECONDS = 10.0  # Draft — Circuit Breaker 재가동 승인 반영 주기


def flag_enabled(name: str) -> bool:
    """운영 기본값은 켜짐. 통합테스트(tests/conftest.py)는 lifespan을 통째로
    띄우므로, 공유 dev DB에 남은 RUNNING 실행을 실제 거래소로 tick하거나
    재시작 복구가 실거래소를 조회하지 않도록 "0"으로 끈다."""
    return os.environ.get(name, "1") != "0"


@dataclass
class BackgroundLoops:
    """lifespan이 시작·정지시키는 백그라운드 태스크 묶음."""

    execution_scheduler: ExecutionLoopScheduler
    tasks: list[asyncio.Task[None]] = field(default_factory=list)

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def start_background_loops(
    *,
    pool: asyncpg.Pool,
    policy: RiskPolicy,
    event_bus: EventBus,
    credential_resolver: CredentialResolver,
    api_tracker: ApiCallTracker,
) -> BackgroundLoops:
    async def _heartbeat_loop() -> None:
        """FD-9.1 — watchdog_process.py(별도 OS 프로세스)가 이 메인 프로세스의
        생사를 판정하는 유일한 신호. 프로세스 메모리를 공유하지 않으므로
        파일 타임스탬프로만 통신한다(core/safety/heartbeat.py)."""
        while True:
            write_heartbeat(DEFAULT_HEARTBEAT_PATH)
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    heartbeat_task = asyncio.create_task(_heartbeat_loop())

    # FD-14(신설) — 가격/지표 알림 평가 루프. heartbeat_loop과 동일 패턴
    # (main.py가 유일한 백그라운드 스케줄러 지점) — 알림 하나 평가가
    # 실패해도(자격증명 해지 등) 다음 알림·다음 주기로 계속 진행한다
    # (alert_service.py::evaluate_all_active 참조).
    alert_service = AlertService(
        pool, credential_resolver=credential_resolver, publish=event_bus.publish
    )

    async def _alert_evaluation_loop() -> None:
        while True:
            await asyncio.sleep(ALERT_EVALUATION_INTERVAL_SECONDS)
            # 레드팀 #2026-09-02-21 — evaluate_all_active()는 개별 알림 실패를
            # 내부에서 이미 건너뛰지만, 이 호출 자체(또는 그 안에서 예상 못한
            # 예외)가 이 루프를 빠져나가면 alert_task 코루틴이 영구히 죽어
            # 재시작 전까지 아무 사용자의 알림도 평가되지 않는다 — 두 번째
            # 방어선으로 여기서도 잡아 다음 주기에 계속 시도한다.
            try:
                await alert_service.evaluate_all_active()
            except Exception:
                logger.exception(
                    "alert_evaluation_loop: 이번 주기 평가 실패 — 다음 주기에 재시도합니다."
                )

    alert_task = asyncio.create_task(_alert_evaluation_loop())

    # ZuluTrade식 "위험 관리" — 실행별 손실 한도(%) 자동 정지 루프. 위 두
    # 루프와 동일 패턴(main.py가 유일한 백그라운드 스케줄러 지점).
    risk_guard_service = RiskGuardService(
        pool,
        ExecutionService(pool, policy, publish=event_bus.publish),
        publish=event_bus.publish,
    )

    async def _risk_guard_loop() -> None:
        while True:
            await asyncio.sleep(RISK_GUARD_INTERVAL_SECONDS)
            # 레드팀 #25 / 전수감사 §2 P1 — alert 루프와 같은 방어선. 이 호출이
            # 예외를 내면 손실 한도 자동정지가 재시작 전까지 영구히 죽는다.
            try:
                await risk_guard_service.evaluate_all_running()
            except Exception:
                logger.exception(
                    "risk_guard_loop: 이번 주기 평가 실패 — 다음 주기에 재시도합니다."
                )

    risk_guard_task = asyncio.create_task(_risk_guard_loop())

    # 05번 §5.6 — 재시작 복구. 백그라운드 루프를 띄우기 전에 1회. 거래소가
    # 응답하지 않아도 앱 기동 자체는 막지 않는다(복구는 다음 틱이 이어받는다).
    if flag_enabled("AIOS_STARTUP_RECOVERY_ENABLED"):
        try:
            await recover_orders_on_startup(
                pool, resolve_adapter=credential_resolver.get_adapter, publish=event_bus.publish
            )
        except Exception:
            logger.exception("restart_recovery: 재시작 복구 실패 — 실행 루프 tick이 이어받습니다.")

    # FD-8 실행 루프 — 전수감사 §3에서 확인된 최대 배선 결함. run_execution_tick은
    # 완전했지만 호출자가 테스트뿐이었다. 주기는 risk_policy.yaml
    # execution_loop.interval_sec(판단 계층 설정의 단일 출처)에서 읽는다.
    execution_scheduler = ExecutionLoopScheduler(
        pool,
        resolve_adapter=credential_resolver.get_adapter,
        policy=policy,
        publish=event_bus.publish,
    )
    execution_loop_task: asyncio.Task[None] | None = None
    if flag_enabled("AIOS_EXECUTION_LOOP_ENABLED"):
        execution_loop_task = asyncio.create_task(execution_scheduler.run_forever())
    else:
        logger.warning(
            "execution_loop: AIOS_EXECUTION_LOOP_ENABLED=0 — 실행 루프를 띄우지 않습니다."
        )

    # FD-9.4b — Circuit Breaker 재가동 승인 반영 + 지표 기반 격상/완화 평가.
    # check_reactivation()만으로는 승인 나간 재가동만 반영될 뿐, 실제
    # 지표(API 오류율·주문 거부율·일손실)가 다시 악화되는 것을 잡지
    # 못한다 — 같은 주기에 evaluate(metrics)를 먼저 돌려 최신 상태를
    # 반영한 뒤 check_reactivation()으로 승인 결과까지 마저 반영한다.
    circuit_breaker = CircuitBreakerService(pool, policy.circuit_breaker, publish=event_bus.publish)

    async def _safety_reactivation_loop() -> None:
        while True:
            await asyncio.sleep(SAFETY_REACTIVATION_INTERVAL_SECONDS)
            try:
                metrics = await collect_circuit_breaker_metrics(pool, api_tracker)
                await circuit_breaker.evaluate(metrics)
                await circuit_breaker.check_reactivation()
            except Exception:
                logger.exception("safety_reactivation_loop: 이번 주기 실패 — 다음 주기에 재시도")

    safety_task = asyncio.create_task(_safety_reactivation_loop())

    tasks = [heartbeat_task, alert_task, risk_guard_task, safety_task]
    if execution_loop_task is not None:
        tasks.append(execution_loop_task)

    return BackgroundLoops(execution_scheduler=execution_scheduler, tasks=tasks)
