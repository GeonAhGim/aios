"""§16.0-B — 안전장치 백그라운드 루프 공통 방어 패턴.

Spec: 16_backend_signatures.md#§16.0-B, L4_platform_observability_tenancy_api_v1.0.md §9 PLT-08.

핵심 원칙: 예외가 발생해도 루프 자체는 절대 종료되지 않는다 — CRITICAL
로그 남기고 다음 주기에 재시도. 안전장치가 예외 하나로 영구히 멈추는 것은
안전장치가 아예 없는 것보다 위험하다(거짓 안전감을 준다는 점에서 오히려
더 나쁨). FD-9의 5개 루프(Watchdog·Split-Brain·Circuit Breaker·Data
Distrust·Reconciliation) 전부가 이 함수로 감싸져 실행된다.

`health`는 선택 인자다(기존 호출부·테스트가 넘기지 않아도 그대로 동작) —
넘기면 매 tick마다 `bind_system(f"safety.{name}")`으로 루프 전용 컨텍스트를
바인딩하고 `LoopHealth.record_tick`으로 결과를 기록한다.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from src.core.observability.context import bind_system
from src.core.observability.loop_health import LoopHealth

logger = logging.getLogger("aios.safety")

TickFn = Callable[[], Awaitable[None]]


async def run_safety_loop(
    name: str,
    interval_sec: float,
    tick_fn: TickFn,
    *,
    health: LoopHealth | None = None,
) -> None:
    """`tick_fn`은 매 주기 1회 실행되는 순수 로직(예: WatchdogService.check_once).
    audit_log(FD-7.2) 기록은 이 함수가 아니라 tick_fn 내부(각 서비스)의 책임 —
    이 함수는 "루프가 죽지 않게" 하는 것만 책임진다."""
    while True:
        start = time.monotonic()
        ok = True
        try:
            with bind_system(f"safety.{name}"):
                await tick_fn()
        except Exception as exc:  # noqa: BLE001 — 의도적으로 모든 예외를 포착
            ok = False
            logger.critical("[%s] 안전장치 루프 예외 — 계속 재시도: %s", name, exc, exc_info=exc)
        if health is not None:
            health.record_tick(name, ok, time.monotonic() - start, interval_sec=interval_sec)
        await asyncio.sleep(interval_sec)
