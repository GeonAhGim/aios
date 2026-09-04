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

§9 PLT-08 — heartbeat/alert/risk_guard/safety_reactivation 4개 루프는
`LoopHealth.record_tick`으로 계측된다(`_run_instrumented` 공용 래퍼). tick마다
`bind_system(f"loop.{name}")`으로 시스템 컨텍스트를 새로 바인딩한다(부모 요청
컨텍스트 누수 방지, `context.py` 모듈독스트링 참조). execution_loop은
`ExecutionLoopScheduler`가 별도 스케줄러라 이 리프 범위 밖이다(선행 리프에서
계측 예정).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import asyncpg

from src.core.event_bus.bus import EventBus
from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.observability.context import bind_system
from src.core.observability.loop_health import LoopHealth, loop_health
from src.core.safety.circuit_breaker import CircuitBreakerService
from src.core.safety.data_distrust import DataDistrustMonitor
from src.core.safety.data_freshness import DataFreshnessTracker
from src.core.safety.heartbeat import DEFAULT_HEARTBEAT_PATH, write_heartbeat
from src.core.safety.metrics_collector import ApiCallTracker
from src.foundation.execution_ownership.adapters.postgres_repository import (
    PostgresExecutionLeaseRepository,
)
from src.foundation.execution_ownership.ports.repository import ExecutionLeaseRepository
from src.services.alert_service import AlertService
from src.services.credential_resolver import CredentialResolver
from src.services.execution_loop.recovery_wiring import recover_orders_on_startup
from src.services.execution_loop.scheduler import ExecutionLoopScheduler
from src.services.execution_service import ExecutionService
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.risk_guard_service import RiskGuardService
from src.services.safety.circuit_breaker_loop import (
    MetricsHistory,
    cooldown_ticks,
    run_circuit_breaker_tick,
)

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
    lease_repo: ExecutionLeaseRepository
    owner_id: str
    tasks: list[asyncio.Task[None]] = field(default_factory=list)

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        # §6 — 정상 종료는 만료(TTL)를 기다리지 않고 즉시 리스를 넘긴다.
        # 위에서 execution_loop_task를 먼저 취소했으므로 release 이후
        # 이 owner_id가 새 리스를 다시 획득하는 레이스는 없다.
        await self.lease_repo.release_all(self.owner_id)


async def _run_instrumented(
    name: str,
    interval_sec: float,
    tick: Callable[[], Awaitable[Any]],
    *,
    health: LoopHealth,
    on_error: str,
) -> None:
    """PLT-08 공용 계측 래퍼 — `bind_system` + `LoopHealth.record_tick`. 예외는
    여기서 삼키고(로그만 남김) 루프 자체는 죽지 않는다(각 루프 원래 동작과 동일)."""
    start = time.monotonic()
    ok = True
    try:
        with bind_system(f"loop.{name}"):
            await tick()
    except Exception:
        ok = False
        logger.exception(on_error)
    finally:
        health.record_tick(name, ok, time.monotonic() - start, interval_sec=interval_sec)


async def run_periodic_loop(
    name: str,
    interval_sec: float,
    tick: Callable[[], Awaitable[Any]],
    *,
    health: LoopHealth,
    on_error: str,
) -> None:
    """`sleep(interval_sec)` → 계측된 tick 1회, 무한 반복. alert/risk_guard/
    safety_reactivation과 main.py의 LedgerIntegrityScheduler 무결성 루프가
    공유하는 공용 루프 본체(export — main.py가 직접 가져다 쓴다)."""
    while True:
        await asyncio.sleep(interval_sec)
        await _run_instrumented(name, interval_sec, tick, health=health, on_error=on_error)


async def start_background_loops(
    *,
    pool: asyncpg.Pool,
    policy: RiskPolicy,
    event_bus: EventBus,
    credential_resolver: CredentialResolver,
    api_tracker: ApiCallTracker,
    freshness_tracker: DataFreshnessTracker | None = None,
    reactivation_history: MetricsHistory | None = None,
    health: LoopHealth | None = None,
) -> BackgroundLoops:
    health = health if health is not None else loop_health()

    async def _heartbeat_loop() -> None:
        """FD-9.1 — watchdog_process.py(별도 OS 프로세스)가 이 메인 프로세스의
        생사를 판정하는 유일한 신호. 프로세스 메모리를 공유하지 않으므로
        파일 타임스탬프로만 통신한다(core/safety/heartbeat.py).

        예외를 삼키지 않는다(다른 3개 루프와 달리) — heartbeat 실패는 watchdog이
        프로세스 사망으로 오판하길 원하는 신호이므로, 원래 동작대로 전파해
        태스크를 죽인다. `LoopHealth`에는 실패로 기록한 뒤 다시 던진다."""
        while True:
            start = time.monotonic()
            ok = True
            try:
                with bind_system("loop.heartbeat"):
                    write_heartbeat(DEFAULT_HEARTBEAT_PATH)
            except Exception:
                ok = False
                raise
            finally:
                health.record_tick(
                    "heartbeat",
                    ok,
                    time.monotonic() - start,
                    interval_sec=HEARTBEAT_INTERVAL_SECONDS,
                )
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    heartbeat_task = asyncio.create_task(_heartbeat_loop())

    # FD-14(신설) — 가격/지표 알림 평가 루프. heartbeat_loop과 동일 패턴
    # (main.py가 유일한 백그라운드 스케줄러 지점) — 알림 하나 평가가
    # 실패해도(자격증명 해지 등) 다음 알림·다음 주기로 계속 진행한다
    # (alert_service.py::evaluate_all_active 참조).
    alert_service = AlertService(
        pool, credential_resolver=credential_resolver, publish=event_bus.publish
    )

    # 레드팀 #2026-09-02-21 — evaluate_all_active()는 개별 알림 실패를 내부에서
    # 이미 건너뛰지만, 이 호출 자체(또는 그 안에서 예상 못한 예외)가 이 루프를
    # 빠져나가면 alert_task 코루틴이 영구히 죽어 재시작 전까지 아무 사용자의
    # 알림도 평가되지 않는다 — 두 번째 방어선으로 `run_periodic_loop`가 잡는다.
    alert_task = asyncio.create_task(
        run_periodic_loop(
            "alert_evaluation",
            ALERT_EVALUATION_INTERVAL_SECONDS,
            alert_service.evaluate_all_active,
            health=health,
            on_error="alert_evaluation_loop: 이번 주기 평가 실패 — 다음 주기에 재시도합니다.",
        )
    )

    # ZuluTrade식 "위험 관리" — 실행별 손실 한도(%) 자동 정지 루프. 위 두
    # 루프와 동일 패턴(main.py가 유일한 백그라운드 스케줄러 지점).
    risk_guard_service = RiskGuardService(
        pool,
        ExecutionService(
            pool,
            policy,
            pre_start_gate=make_foundation_pre_submit_gate(pool),
            publish=event_bus.publish,
        ),
        publish=event_bus.publish,
    )

    # 레드팀 #25 / 전수감사 §2 P1 — alert 루프와 같은 방어선. 이 호출이 예외를
    # 내면 손실 한도 자동정지가 재시작 전까지 영구히 죽는다.
    risk_guard_task = asyncio.create_task(
        run_periodic_loop(
            "risk_guard",
            RISK_GUARD_INTERVAL_SECONDS,
            risk_guard_service.evaluate_all_running,
            health=health,
            on_error="risk_guard_loop: 이번 주기 평가 실패 — 다음 주기에 재시도합니다.",
        )
    )

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
    # EO-03 최소 배선 — 리스 갱신 주기·kill switch 해제 시점의 release_all
    # 연결·적대적(중복 tick 방지) 테스트는 EO-04 범위(§9)로 남긴다. 여기서는
    # 신규 필수 인자 없이는 컴파일조차 되지 않는 시그니처(I-01)를 기존
    # 컴포넌트(foundation_gate.py/data_distrust.py/postgres_repository.py,
    # 전부 이미 완성)로만 채운다 — 새 구현은 만들지 않는다.
    owner_id = f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"
    lease_repo = PostgresExecutionLeaseRepository(pool)
    execution_scheduler = ExecutionLoopScheduler(
        pool,
        resolve_adapter=credential_resolver.get_adapter,
        policy=policy,
        publish=event_bus.publish,
        pre_submit_gate=make_foundation_pre_submit_gate(pool),
        distrust_monitor=DataDistrustMonitor(publish=event_bus.publish),
        lease_repo=lease_repo,
        owner_id=owner_id,
    )
    execution_loop_task: asyncio.Task[None] | None = None
    if flag_enabled("AIOS_EXECUTION_LOOP_ENABLED"):
        execution_loop_task = asyncio.create_task(execution_scheduler.run_forever())
    else:
        logger.warning(
            "execution_loop: AIOS_EXECUTION_LOOP_ENABLED=0 — 실행 루프를 띄우지 않습니다."
        )

    # R-45 — circuit_breaker_loop.run_circuit_breaker_tick(수집→evaluate→
    # recovery_gate→check_reactivation)을 그대로 돌린다. `history`는 프로세스
    # 수명 동안 유지되는 이력 버퍼 — main.py가 안 넘기면 여기서 만든다.
    circuit_breaker = CircuitBreakerService(pool, policy.circuit_breaker, publish=event_bus.publish)
    history = (
        reactivation_history
        if reactivation_history is not None
        else deque(maxlen=cooldown_ticks(policy))
    )

    async def _safety_tick() -> None:
        await run_circuit_breaker_tick(
            pool, circuit_breaker, api_tracker, freshness_tracker, policy, history=history
        )

    safety_task = asyncio.create_task(
        run_periodic_loop(
            "safety_reactivation",
            SAFETY_REACTIVATION_INTERVAL_SECONDS,
            _safety_tick,
            health=health,
            on_error="safety_reactivation_loop: 이번 주기 실패 — 다음 주기에 재시도",
        )
    )

    tasks = [heartbeat_task, alert_task, risk_guard_task, safety_task]
    if execution_loop_task is not None:
        tasks.append(execution_loop_task)

    return BackgroundLoops(
        execution_scheduler=execution_scheduler,
        lease_repo=lease_repo,
        owner_id=owner_id,
        tasks=tasks,
    )
