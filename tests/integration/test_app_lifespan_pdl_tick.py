"""personal_daily_loss_monitor 틱 실패주입 테스트 — lifespan 격리.

task-19994: test_lifespan_personal_daily_loss_tick_failure_logs_and_survives를
sibling 파일로 분리(§6-12, _<aspect>.py 규칙).
"""

from __future__ import annotations

import asyncio

import pytest

import src.foundation.risk.application.personal_daily_loss_monitor as pdl_monitor_module
from src.core.event_bus.in_process import InProcessEventBus
from src.core.observability.loop_health import LoopHealth, loop_health, set_loop_health
from src.main import app


async def test_lifespan_personal_daily_loss_tick_failure_logs_and_survives(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """실패주입(finding-id 115 재발 방지, task-6510): personal_daily_loss_monitor
    틱이 매번 예외를 던져도 (1) 앱 기동(lifespan 진입) 자체는 실패하지 않고,
    (2) `background_loops._run_instrumented`가 예외를 삼켜 로그로만 남기며,
    (3) 루프 태스크는 죽지 않고 다음 주기에 재시도한다는 것을 확인한다.

    로그 확인에 `caplog` 대신 `capsys`를 쓴다 — `src.main.lifespan()`이
    `configure_logging()`을 호출해 root logger의 핸들러를 통째로 갈아끼우므로
    (`root.handlers.clear()`, src/core/logging/schema.py:148), 진입 전에 붙여둔
    caplog 핸들러가 lifespan 진입과 동시에 떨어져 나가 아무것도 못 잡는다.
    실제 로그는 `QueueListener`가 별도 스레드에서 stderr로 JSON lines를 쓰므로
    (같은 파일 §116 docstring), lifespan 종료 후(`log_listener.stop()`이
    큐를 flush) capsys로 stderr를 읽어야 안정적으로 잡힌다.

    간격 상수는 `start_personal_daily_loss_monitor_task` 호출 시점(lifespan
    진입 시)에 한 번 읽히므로, lifespan에 들어가기 *전에* monkeypatch해야
    한다. `loop_health()`는 프로세스 싱글턴이라 테스트 전용 인스턴스로
    바꿔치기하고 finally에서 원상복구한다(다른 테스트로 상태가 새지 않게)."""
    monkeypatch.setattr(pdl_monitor_module, "PERSONAL_DAILY_LOSS_MONITOR_INTERVAL_SECONDS", 0.05)

    def _always_fails(*args: object, **kwargs: object) -> None:
        raise RuntimeError("personal_daily_loss tick boom")

    monkeypatch.setattr(pdl_monitor_module, "run_personal_daily_loss_monitor_tick", _always_fails)

    test_health = LoopHealth()
    original_health = loop_health()
    set_loop_health(test_health)
    try:
        async with app.router.lifespan_context(app):
            # 기동 자체는 성공한다 — pool/event_bus가 정상 배선된다.
            assert isinstance(app.state.event_bus, InProcessEventBus)
            assert not app.state.pool._closed

            for _ in range(100):
                snapshot = test_health.snapshot()
                status = snapshot.get("personal_daily_loss_monitor")
                if status is not None and status.consecutive_failures >= 2:
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError(
                    "personal_daily_loss_monitor 루프가 시한 안에 2회 이상"
                    " 실패 tick을 기록하지 않았다 — 실패주입이 안 먹혔거나"
                    " 루프가 첫 실패 후 죽었다"
                )

            # 성공 tick은 한 번도 없어야 한다 — 매 틱이 실패로 주입됐다.
            assert test_health.snapshot()["personal_daily_loss_monitor"].last_success_at is None
            # 앱은 정상적으로 계속 실행 중이다(다른 배선에 영향 없음).
            assert await app.state.pool.fetchval("SELECT 1") == 1

        # lifespan 종료 → log_listener.stop()이 큐를 flush했으므로 이제 안전하게 읽는다.
        captured = capsys.readouterr()
        assert "personal_daily_loss_monitor_loop: 이번 주기 실패 -- 재시도합니다." in captured.err
    finally:
        set_loop_health(original_health)
