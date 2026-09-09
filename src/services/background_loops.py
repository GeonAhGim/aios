"""16번대 — main.py lifespan의 백그라운드 루프(heartbeat/alert/risk_guard/
execution_loop/safety/liquidation) 생성·복구·취소.

Spec: 16_backend_signatures.md, ADR-2026-08-10-B, P6(파일당 300줄 초과 금지)

편차: task-117은 원래 src/app/background_loops.py에 두려 했지만, .aios-zone이
`src/app/**`를 선언하지 않아(zone 정책 수정은 에이전트 금지, P8) 이미
SCAFFOLD로 선언된 `src/services/**` 아래로 대신 둔다.

main.py는 pool/event_bus/credential_resolver 등을 조립한 뒤
:func:`start_background_loops`에 넘겨 루프를 띄우고, shutdown 시 반환된
:class:`BackgroundLoops`의 :meth:`~BackgroundLoops.stop`만 호출한다.

§9 PLT-08 — heartbeat/alert/risk_guard/safety_reactivation/liquidation_worker
는 `LoopHealth.record_tick`으로 계측된다(공용 `_run_instrumented` 래퍼).
execution_loop만 별도 스케줄러(`ExecutionLoopScheduler`)로 이 leaf 밖이다.
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
from datetime import datetime, timezone
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
from src.exchanges.bitget.adapter import BitgetAdapter
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.execution_ownership.adapters.postgres_repository import (
    PostgresExecutionLeaseRepository,
)
from src.foundation.execution_ownership.ports.repository import ExecutionLeaseRepository
from src.foundation.paper_control.adapters.postgres_repository import PostgresPaperControlRepository
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.services.alert_service import AlertService
from src.services.credential_resolver import CredentialResolver
from src.services.execution_loop.recovery_wiring import run_startup_recovery_gated
from src.services.execution_loop.scheduler import ExecutionLoopScheduler
from src.services.oms.application.restart_recovery import make_recovery_gate
from src.services.oms.application.wiring import start_outbox_dispatcher_task
from src.services.order_service import fenced_submit_wiring as fsw
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.risk_guard_service import RiskGuardService
from src.services.safety.circuit_breaker_loop import (
    MetricsHistory,
    cooldown_ticks,
    run_circuit_breaker_tick,
)
from src.services.safety.kill_switch_service import KillSwitchService
from src.services.safety.liquidation_executor import run_liquidation_worker_once

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 2.0  # Draft — watchdog_process.py의 5초 폴링 주기보다 짧게
ALERT_EVALUATION_INTERVAL_SECONDS = 60.0  # Draft — 가격/지표 알림 평가 주기
RISK_GUARD_INTERVAL_SECONDS = 30.0  # Draft — 손실 한도 자동정지 평가 주기
SAFETY_REACTIVATION_INTERVAL_SECONDS = 10.0  # Draft — Circuit Breaker 재가동 승인 반영 주기
LIQUIDATION_WORKER_INTERVAL_SECONDS = 3.0  # Draft — slice.not_before 최소 간격(2s)보다 촘촘히


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
        # §6 — 정상 종료는 TTL을 기다리지 않고 즉시 리스를 넘긴다(execution_loop_task를
        # 먼저 취소했으므로 release 이후 새 리스를 다시 획득하는 레이스는 없다).
        await self.lease_repo.release_all(self.owner_id)


async def _run_instrumented(
    name: str,
    interval_sec: float,
    tick: Callable[[], Awaitable[Any]],
    *,
    health: LoopHealth,
    on_error: str,
) -> None:
    """PLT-08 공용 계측 래퍼 — 예외는 여기서 삼키고(로그만) 루프는 죽지 않는다."""
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
    """`sleep(interval_sec)` → 계측된 tick 1회, 무한 반복(export — main.py도 직접 쓴다)."""
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
        """FD-9.1 — watchdog_process.py(별도 OS 프로세스)가 메인 프로세스 생사를
        판정하는 유일한 신호(파일 타임스탬프, core/safety/heartbeat.py). 다른
        루프와 달리 예외를 삼키지 않는다 — watchdog이 실패를 곧바로 감지하도록
        `LoopHealth`에 기록한 뒤 그대로 다시 던져 태스크를 죽인다."""
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
                elapsed = time.monotonic() - start
                health.record_tick(
                    "heartbeat", ok, elapsed, interval_sec=HEARTBEAT_INTERVAL_SECONDS
                )
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    heartbeat_task = asyncio.create_task(_heartbeat_loop())

    # FD-14 — 가격/지표 알림 평가 루프(알림 하나 실패해도 다음 주기로 계속).
    alert_service = AlertService(
        pool, credential_resolver=credential_resolver, publish=event_bus.publish
    )

    # 레드팀 #2026-09-02-21 — evaluate_all_active() 자체가 예외를 내면 alert_task가
    # 영구히 죽는다 — 두 번째 방어선으로 `run_periodic_loop`가 잡는다.
    alert_task = asyncio.create_task(
        run_periodic_loop(
            "alert_evaluation",
            ALERT_EVALUATION_INTERVAL_SECONDS,
            alert_service.evaluate_all_active,
            health=health,
            on_error="alert_evaluation_loop: 이번 주기 평가 실패 — 다음 주기에 재시도합니다.",
        )
    )

    # R-41 손실 한도(%) 자동 정지 루프 — 실제 정지는 KillSwitchService(R-40)에 위임.
    # 시스템 전역 자격증명이 없어 exchange_adapters는 빈 매핑(sweeper fan-out 비치명적).
    kill_switch_service = KillSwitchService(
        risk_gate_repo=PostgresRiskGateRepository(pool), pg_pool=pool,
        paper_control_repo=PostgresPaperControlRepository(pool),
        exchange_adapters={}, audit_repo=PostgresAuditEventRepository(pool),
    )
    risk_guard_service = RiskGuardService(pool, kill_switch_service, publish=event_bus.publish)

    # 레드팀 #25 — alert 루프와 같은 방어선(이 호출 자체가 예외를 내면 영구히 죽는다).
    risk_guard_task = asyncio.create_task(
        run_periodic_loop(
            "risk_guard",
            RISK_GUARD_INTERVAL_SECONDS,
            risk_guard_service.evaluate_all_running,
            health=health,
            on_error="risk_guard_loop: 이번 주기 평가 실패 — 다음 주기에 재시도합니다.",
        )
    )

    # Doc 05 §5.6 + task-2151(L4-18a) — startup recovery, once before the background loops.
    # See recovery_wiring.py for behavior when the flag is off / on failure (fail-closed).
    recovery_state = await run_startup_recovery_gated(
        pool,
        resolve_adapter=credential_resolver.get_adapter,
        publish=event_bus.publish,
        enabled=flag_enabled("AIOS_STARTUP_RECOVERY_ENABLED"),
    )

    # FD-8 execution loop -- EO-03 minimal wiring (adversarial tests left to EO-04).
    owner_id = f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"
    lease_repo = PostgresExecutionLeaseRepository(pool)
    execution_scheduler = ExecutionLoopScheduler(
        pool,
        resolve_adapter=credential_resolver.get_adapter,
        policy=policy,
        publish=event_bus.publish,
        pre_submit_gate=make_recovery_gate(
            recovery_state, make_foundation_pre_submit_gate(pool, require_mandate=False)
        ),
        distrust_monitor=DataDistrustMonitor(publish=event_bus.publish),
        lease_repo=lease_repo,
        owner_id=owner_id,
        fence_reader_factory=fsw.make_fence_reader_factory(pool),  # task-1717 P0-D
        decision_reader=fsw.make_decision_reader(pool),
    )
    execution_loop_task: asyncio.Task[None] | None = None
    if flag_enabled("AIOS_EXECUTION_LOOP_ENABLED"):
        execution_loop_task = asyncio.create_task(execution_scheduler.run_forever())
    else:
        logger.warning(
            "execution_loop: AIOS_EXECUTION_LOOP_ENABLED=0 — 실행 루프를 띄우지 않습니다."
        )
    oms_dispatcher_task = start_outbox_dispatcher_task(pool, credential_resolver.get_adapter)
    # R-45 — run_circuit_breaker_tick(수집→evaluate→recovery_gate→check_reactivation).
    # `history`는 프로세스 수명 동안 유지되는 이력 버퍼(안 넘기면 여기서 만든다).
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

    # R-52(task-2358) -- adapters keyed by exchange, not per-user: a GLOBAL
    # liquidation_request has no single owning tenant (same reasoning as
    # kill_switch_service's exchange_adapters above).
    liquidation_adapters = {"bitget": BitgetAdapter("", "", "", demo_mode=True)}

    async def _liquidation_tick() -> None:
        now = datetime.now(timezone.utc)
        await run_liquidation_worker_once(pool, liquidation_adapters, now=now)

    liquidation_task: asyncio.Task[None] | None = None
    if flag_enabled("AIOS_LIQUIDATION_WORKER_ENABLED"):
        liquidation_task = asyncio.create_task(
            run_periodic_loop(
                "liquidation_worker", LIQUIDATION_WORKER_INTERVAL_SECONDS, _liquidation_tick,
                health=health, on_error="liquidation_worker_loop: 이번 주기 실패 — 재시도",
            )
        )

    tasks = [heartbeat_task, alert_task, risk_guard_task, safety_task]
    tasks.extend(
        t for t in (execution_loop_task, oms_dispatcher_task, liquidation_task) if t is not None
    )

    return BackgroundLoops(
        execution_scheduler=execution_scheduler,
        lease_repo=lease_repo,
        owner_id=owner_id,
        tasks=tasks,
    )
