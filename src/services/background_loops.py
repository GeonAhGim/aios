"""Task series 16xx — creation/recovery/cancellation of main.py lifespan's background loops
(heartbeat/alert/risk_guard/execution_loop/safety/liquidation/post_trade_batch).
Spec: 16_backend_signatures.md, ADR-2026-08-10-B, P6 (300-line cap), CM-11.
Deviation: task-117 wanted src/app/background_loops.py, but .aios-zone doesn't declare src/app/**
(P8 -- agents may not modify zone policy), so this lives under src/services/** (SCAFFOLD) instead.
main.py assembles pool/event_bus/credential_resolver etc. and passes them to
:func:`start_background_loops`. On shutdown it calls only the returned
:class:`BackgroundLoops`.stop. Every loop is instrumented via `LoopHealth.record_tick`, except
execution_loop, which has its own scheduler (`ExecutionLoopScheduler`) outside this leaf.
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
from datetime import datetime, timedelta, timezone
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
from src.foundation.mandates.application.evaluate_post_trade import run_daily_post_trade_batch
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
from src.services.personal_daily_loss_loop import start_personal_daily_loss_monitor_task
from src.services.risk_guard_service import RiskGuardService
from src.services.safety.circuit_breaker_loop import (
    MetricsHistory,
    cooldown_ticks,
    run_circuit_breaker_tick,
)
from src.services.safety.kill_switch_service import KillSwitchService
from src.services.safety.liquidation_executor import run_liquidation_worker_once
from src.services.safety.reference_quotes import DefaultDistrustProviderFactory

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 2.0  # Draft — shorter than watchdog_process.py's 5s poll interval
ALERT_EVALUATION_INTERVAL_SECONDS = 60.0  # Draft — price/indicator alert evaluation interval
RISK_GUARD_INTERVAL_SECONDS = 30.0  # Draft — loss-limit auto-stop evaluation interval
SAFETY_REACTIVATION_INTERVAL_SECONDS = 10.0  # Draft — circuit breaker reactivation approval
LIQUIDATION_WORKER_INTERVAL_SECONDS = 3.0  # Draft — tighter than slice.not_before's min (2s)
POST_TRADE_BATCH_INTERVAL_SECONDS = 86400.0  # task-2616 -- CM-11 once daily (was 3600.0/hourly)


def flag_enabled(name: str) -> bool:
    """Default is on. Integration tests (tests/conftest.py) boot the whole lifespan, so this is
    switched off with "0" to keep real-exchange ticks/lookups off the shared dev DB."""
    return os.environ.get(name, "1") != "0"


@dataclass
class BackgroundLoops:
    """Bundle of background tasks that lifespan starts and stops."""

    execution_scheduler: ExecutionLoopScheduler
    lease_repo: ExecutionLeaseRepository
    owner_id: str
    tasks: list[asyncio.Task[None]] = field(default_factory=list)
    trigger_post_trade_batch: Callable[[], Awaitable[None]] | None = None  # task-2616 on-demand

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        # §6 — a graceful shutdown hands off the lease immediately instead of waiting for the TTL
        # (execution_loop_task cancelled first, so no race re-acquires a lease after release).
        await self.lease_repo.release_all(self.owner_id)


async def _run_instrumented(
    name: str, interval_sec: float, tick: Callable[[], Awaitable[Any]], *,
    health: LoopHealth, on_error: str,
) -> None:
    """PLT-08 shared instrumentation wrapper — swallows exceptions (logs only) so loop survives."""
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
    name: str, interval_sec: float, tick: Callable[[], Awaitable[Any]], *,
    health: LoopHealth, on_error: str,
) -> None:
    """`sleep(interval_sec)` -> one instrumented tick, forever (exported — main.py uses it too)."""
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
        """FD-9.1 — the only signal watchdog_process.py uses to judge whether the main process
        is alive (file timestamp). Unlike the other loops, this does not swallow exceptions —
        it records to `LoopHealth` and re-raises to kill the task so watchdog detects it fast."""
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

    # FD-14 — price/indicator alert evaluation loop (one alert failing still continues next cycle).
    alert_service = AlertService(
        pool, credential_resolver=credential_resolver, publish=event_bus.publish)

    # Red team #2026-09-02-21 — even if evaluate_all_active() raises, run_periodic_loop catches it.
    alert_task = asyncio.create_task(
        run_periodic_loop(
            "alert_evaluation", ALERT_EVALUATION_INTERVAL_SECONDS,
            alert_service.evaluate_all_active, health=health,
            on_error="alert_evaluation_loop: 이번 주기 평가 실패 — 다음 주기에 재시도합니다.",
        )
    )

    # R-41 loss-limit (%) auto-stop — actual stop delegated to KillSwitchService (R-40); no
    # global credentials, so exchange_adapters is empty. risk_gate_repo is shared by CM-11 too.
    risk_gate_repo = PostgresRiskGateRepository(pool)
    kill_switch_service = KillSwitchService(
        risk_gate_repo=risk_gate_repo, pg_pool=pool,
        paper_control_repo=PostgresPaperControlRepository(pool),
        exchange_adapters={}, audit_repo=PostgresAuditEventRepository(pool),
    )
    risk_guard_service = RiskGuardService(pool, kill_switch_service, publish=event_bus.publish)

    # Red team #25 — same defense line as the alert loop (a raise here kills it permanently).
    risk_guard_task = asyncio.create_task(
        run_periodic_loop(
            "risk_guard", RISK_GUARD_INTERVAL_SECONDS,
            risk_guard_service.evaluate_all_running, health=health,
            on_error="risk_guard_loop: 이번 주기 평가 실패 — 다음 주기에 재시도합니다.",
        )
    )

    personal_daily_loss_task = start_personal_daily_loss_monitor_task(
        pool, health=health, run_periodic_loop=run_periodic_loop
    )

    # Doc 05 §5.6 + task-2151(L4-18a) — one-time startup recovery before the background loops
    # (see recovery_wiring.py for the fail-closed behavior on failure).
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
        pool, resolve_adapter=credential_resolver.get_adapter, policy=policy,
        publish=event_bus.publish,
        pre_submit_gate=make_recovery_gate(
            recovery_state, make_foundation_pre_submit_gate(pool, require_mandate=True)
        ),
        distrust_monitor=DataDistrustMonitor(publish=event_bus.publish),
        distrust_provider_factory=DefaultDistrustProviderFactory(),  # R-48/task-2810
        lease_repo=lease_repo, owner_id=owner_id,
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
    # R-45 — collect -> evaluate -> recovery_gate -> check_reactivation. history is a history
    # buffer kept alive for the process lifetime (created here if not passed in).
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
            "safety_reactivation", SAFETY_REACTIVATION_INTERVAL_SECONDS, _safety_tick,
            health=health, on_error="safety_reactivation_loop: 이번 주기 실패 — 다음 주기에 재시도",
        )
    )

    # R-52(task-2358) -- GLOBAL liquidation_request has no single owning tenant, so it maps
    # per exchange (same reason as kill_switch_service's exchange_adapters).
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

    # CM-11/12 -- a DENY blocks(KillSwitchService) + notifies. See test_post_trade_batch.py.
    async def _post_trade_batch_tick() -> None:
        now = datetime.now(timezone.utc)
        business_date = (now - timedelta(days=1)).date()
        await run_daily_post_trade_batch(
            pool, kill_switch_service, risk_gate_repo, business_date=business_date, now=now,
            publish=event_bus.publish,
        )

    post_trade_batch_task: asyncio.Task[None] | None = None
    if flag_enabled("AIOS_POST_TRADE_BATCH_ENABLED"):
        post_trade_batch_task = asyncio.create_task(
            run_periodic_loop(
                "post_trade_batch", POST_TRADE_BATCH_INTERVAL_SECONDS, _post_trade_batch_tick,
                health=health, on_error="post_trade_batch_loop: 이번 주기 실패 — 재시도",
            )
        )

    tasks = [heartbeat_task, alert_task, risk_guard_task, safety_task, personal_daily_loss_task]
    optional = (execution_loop_task, oms_dispatcher_task, liquidation_task, post_trade_batch_task)
    tasks.extend(t for t in optional if t is not None)

    return BackgroundLoops(
        execution_scheduler=execution_scheduler,
        lease_repo=lease_repo,
        owner_id=owner_id,
        tasks=tasks,
        trigger_post_trade_batch=_post_trade_batch_tick if post_trade_batch_task else None,
    )
