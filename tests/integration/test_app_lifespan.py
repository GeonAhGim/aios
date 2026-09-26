"""16번대 통합테스트 — src/main.py lifespan.

background_loops.py 분리(P6) 후에도 lifespan의 동작(app.state 배선, 백그라운드
루프 시작·정지, pool/event_bus 정리)이 그대로인지 확인한다. 실제 dev DB에
연결한다(tests/conftest.py의 TEST_DATABASE_URL).

성능 단언(DoD): lifespan 초기화 p99 < 5s, 정리 p99 < 2s (ADR-2026-09-09-C).
"""

from __future__ import annotations

import asyncio
import time
from logging.handlers import QueueListener
from unittest.mock import patch

import pytest

import src.foundation.risk.application.personal_daily_loss_monitor as pdl_monitor_module
from src.core.event_bus.in_process import InProcessEventBus
from src.core.observability.loop_health import LoopHealth, loop_health, set_loop_health
from src.main import app
from src.services.credential_resolver import CredentialResolver
from src.services.execution_loop.scheduler import ExecutionLoopScheduler
from src.services.safety.reference_quotes import (
    BinancePublicTickerReference,
    BitgetFuturesMarkPriceReference,
    DefaultDistrustProviderFactory,
)


def _defined_in_background_loops(task: asyncio.Task[None]) -> bool:
    code = getattr(task.get_coro(), "cr_code", None)
    return code is not None and "background_loops.py" in code.co_filename


async def test_lifespan_wires_real_distrust_provider_factory_into_execution_scheduler() -> None:
    """게이트 적색 재현(task-2810 배선 결함 회귀) — 예전엔
    `ExecutionLoopScheduler`가 `distrust_provider_factory` 인자 자체를
    받지 않아 production 배선(`background_loops.py`)이 이 값을 채울 방법이
    없었고, 매 틱 `distrust_providers`가 기본값 `()`로 고정돼 R-48의 2소스
    쿼럼 비교가 한 번도 돌지 않았다. `tests/integration/test_execution_scheduler.py`
    의 관련 테스트들은 스케줄러를 직접 만들며 커스텀 팩토리를 주입해 이
    회귀를 못 잡는다 — 여기서는 실제 lifespan(`start_background_loops`)이
    조립한 스케줄러의 팩토리가 `DefaultDistrustProviderFactory`이고
    실제로 참조 provider를 만들어내는지 end-to-end로 증명한다."""
    async with app.router.lifespan_context(app):
        scheduler = app.state.execution_scheduler
        factory = scheduler._distrust_provider_factory
        assert isinstance(factory, DefaultDistrustProviderFactory)

        bitget_providers = factory(object(), "bitget")
        assert len(bitget_providers) == 2
        assert isinstance(bitget_providers[0], BinancePublicTickerReference)
        assert isinstance(bitget_providers[1], BitgetFuturesMarkPriceReference)

        # bitget이 아닌 거래소는 아직 R-48 쿼럼 대상이 아니다(Binance만 참조).
        other_providers = factory(object(), "okx")
        assert len(other_providers) == 1
        assert isinstance(other_providers[0], BinancePublicTickerReference)


async def test_lifespan_wires_app_state_and_starts_background_loops() -> None:
    tasks_before = asyncio.all_tasks()

    async with app.router.lifespan_context(app):
        assert isinstance(app.state.event_bus, InProcessEventBus)
        assert isinstance(app.state.credential_resolver, CredentialResolver)
        assert isinstance(app.state.execution_scheduler, ExecutionLoopScheduler)
        assert not app.state.pool._closed

        # heartbeat/alert/risk_guard/safety/personal_daily_loss_monitor 루프
        # (conftest.py가 AIOS_EXECUTION_LOOP_ENABLED=0으로 실행 루프는 꺼둔다).
        # personal_daily_loss_task는 task-6510부터 항상 배선된다(src/main.py가
        # start_personal_daily_loss_monitor_task를 무조건 호출) — src/services/
        # personal_daily_loss_loop.py에 정의돼 있지만, 그 함수가 만드는 태스크의
        # 코루틴은 인자로 받은 background_loops.run_periodic_loop이므로
        # _defined_in_background_loops가 여전히 이 태스크를 잡아낸다.
        # InProcessEventBus의 내부 워커 태스크 등 다른 신규 태스크와 구분하기
        # 위해 background_loops.py에 정의된 코루틴만 골라낸다.
        new_tasks = asyncio.all_tasks() - tasks_before
        loop_tasks = {task for task in new_tasks if _defined_in_background_loops(task)}
        assert len(loop_tasks) == 5
        assert all(not task.done() for task in loop_tasks)

        pool = app.state.pool
        assert await pool.fetchval("SELECT 1") == 1

    # 종료 시 루프가 전부 취소·수거되고 pool이 닫힌다.
    assert all(task.done() for task in loop_tasks)
    assert pool._closed


async def test_lifespan_can_start_and_stop_twice() -> None:
    """app 재시작(예: 여러 테스트 모듈의 client 픽스처)에도 잔여 태스크가
    누적되지 않는지 — 백그라운드 루프 취소 로직이 매번 완전히 정리하는지 확인."""
    tasks_before = asyncio.all_tasks()

    async with app.router.lifespan_context(app):
        pass
    async with app.router.lifespan_context(app):
        pass

    tasks_after = asyncio.all_tasks()
    assert tasks_after - tasks_before == set()


@pytest.mark.perf
async def test_lifespan_startup_performance_meets_budget() -> None:
    """성능 단언(DoD): lifespan 초기화/정리 시간이 예산 내인지 확인한다.
    ADR-2026-09-09-C 성능 예산 기준:
    - startup p99 < 5s (pool 생성, event_bus 초기화, background loops 시작)
    - shutdown p99 < 2s (tasks cancel, pool.close, event_bus.stop)

    3회 반복 측정 후 p99 계산."""
    startup_times: list[float] = []
    shutdown_times: list[float] = []

    for _ in range(3):
        start = time.perf_counter()
        async with app.router.lifespan_context(app):
            startup = time.perf_counter() - start
            startup_times.append(startup)
        shutdown = time.perf_counter() - start - startup
        shutdown_times.append(shutdown)

    p99_startup = sorted(startup_times)[-1]  # 3회 중 최대값 ≈ p99
    p99_shutdown = sorted(shutdown_times)[-1]

    assert p99_startup < 5.0, (
        f"lifespan startup p99={p99_startup:.2f}s exceeded budget 5s (times={startup_times})"
    )
    assert p99_shutdown < 2.0, (
        f"lifespan shutdown p99={p99_shutdown:.2f}s exceeded budget 2s (times={shutdown_times})"
    )


# ── Negative tests ──────────────────────────────────────────────────────────


async def test_lifespan_rejects_invalid_database_url() -> None:
    """불변식 위반 입력: 유효하지 않은 DB URL을 넣으면 lifespan이 예외로
    거부해야 한다. pool/event_bus/루프가 부분 조립된 상태로 방치되지
    않음을 확인한다.

    P6 위반 해소: src/main.py — load_env_secrets가 raise하면 create_pool이
    호출되지 않으므로 event_bus가 등록되지 않는다. task-7633: load_env_secrets
    호출 자체가 try/finally 범위 안으로 옮겨졌으므로, 이 실패 경로에서도
    finally가 실행되어 log_listener.stop()이 호출됨을 관측 가능하게 확인한다
    (회귀 시 QueueListener 스레드가 절대 멈추지 않는다).
    """
    import src.main as main_module

    def _broken_secrets() -> None:
        raise ValueError("invalid database URL")

    with patch.object(main_module, "load_env_secrets", _broken_secrets):
        from fastapi import FastAPI

        from src.main import lifespan as _lifespan

        test_app = FastAPI(lifespan=_lifespan)
        captured_listeners: list[QueueListener] = []
        original_configure_logging = main_module.configure_logging

        def _capturing_configure_logging(
            level: str = "INFO", *, redact: bool = True
        ) -> QueueListener:
            listener = original_configure_logging(level, redact=redact)
            captured_listeners.append(listener)
            return listener

        with patch.object(main_module, "configure_logging", _capturing_configure_logging):
            with pytest.raises(ValueError):
                async with test_app.router.lifespan_context(test_app):
                    pass
            assert len(captured_listeners) == 1
            assert captured_listeners[0]._thread is None
        # secrets 로딩 실패 → create_pool 미호출 → event_bus/pool 미등록.
        assert not hasattr(test_app.state, "event_bus")
        assert not hasattr(test_app.state, "pool")


async def test_lifespan_rejects_pool_creation_failure() -> None:
    """실패주입: asyncpg.create_pool 이 raised하면 lifespan이 전체를
    롤백하고 app.state에 부분 상태를 남기지 않는다. — I-01(실패 닫힘).

    task-7633: create_pool 호출 자체가 try/finally 범위 안으로 옮겨졌으므로,
    이 실패 경로에서도 finally가 실행되어 log_listener.stop()이 호출됨을
    관측 가능하게 확인한다(회귀 시 QueueListener 스레드가 절대 멈추지 않는다).
    """
    import asyncpg

    import src.main as main_module

    def _fail_pool(*args: object, **kwargs: object) -> None:
        # asyncpg.create_pool는 regular function (coroutine function 아님) —
        # side_effect로 raise하면 await에서 실제 예외가 올라온다.
        raise asyncpg.PostgresError("connection refused")

    from fastapi import FastAPI

    from src.main import lifespan as _lifespan

    test_app = FastAPI(lifespan=_lifespan)
    captured_listeners: list[QueueListener] = []
    original_configure_logging = main_module.configure_logging

    def _capturing_configure_logging(level: str = "INFO", *, redact: bool = True) -> QueueListener:
        listener = original_configure_logging(level, redact=redact)
        captured_listeners.append(listener)
        return listener

    with patch.object(main_module, "configure_logging", _capturing_configure_logging):
        with patch.object(asyncpg, "create_pool", _fail_pool):
            with pytest.raises(asyncpg.PostgresError):
                async with test_app.router.lifespan_context(test_app):
                    pass
        assert len(captured_listeners) == 1
        assert captured_listeners[0]._thread is None
    # 롤백됐으므로 state에 pool/event_bus가 없어야 한다.
    assert not hasattr(test_app.state, "pool")
    assert not hasattr(test_app.state, "event_bus")


async def test_lifespan_rejects_missing_credential_encryption_key() -> None:
    """불변식 위반 입력: credential encryption key가 비어 있으면 lifespan이
    거부해야 한다. key_ring.from_legacy_hex()가 빈 키를 받지 않도록
    방어한다.

    P6 위반 해소: src/main.py — 예외 범위를 ValueError에서 KeyRingConfigError로
    좁히고, pool 생성 이후 실패한 경우에도(pool은 이미 만들어졌다) finally
    블록이 정상 실행되어 pool.close()가 호출되고 app.state에 pool/event_bus가
    부분 등록되지 않음을 확인한다.
    """
    from pydantic import SecretStr

    from src.data.models.trading import SecretBundle

    def _empty_key_secrets() -> SecretBundle:
        return SecretBundle(
            database_url=SecretStr("postgresql://x"),
            jwt_secret_key=SecretStr("x"),
            credential_encryption_key=SecretStr(""),
            # 빈 키 → _decode_key에서 0바이트 → KeyRingConfigError
            bitget_api_key=SecretStr("x"),
            bitget_api_secret=SecretStr("x"),
            kis_app_key=SecretStr("x"),
            kis_app_secret=SecretStr("x"),
        )

    class _FakePool:
        def __init__(self) -> None:
            self.close_called = False

        async def close(self) -> None:
            self.close_called = True

    fake_pool = _FakePool()

    async def _fake_create_pool(*args: object, **kwargs: object) -> _FakePool:
        return fake_pool

    with patch("src.main.load_env_secrets", _empty_key_secrets):
        from fastapi import FastAPI

        from src.main import lifespan as _lifespan

        test_app = FastAPI(lifespan=_lifespan)
        # 빈 키는 KeyRing.from_legacy_hex()에서 KeyRingConfigError를 raise해야 함.
        from src.core.security.key_ring import KeyRingConfigError

        with patch("src.main.asyncpg.create_pool", _fake_create_pool):
            with pytest.raises(KeyRingConfigError):
                async with test_app.router.lifespan_context(test_app):
                    pass
        # finally 블록이 굴러가므로 pool.close()가 실제로 호출되고,
        # pool/event_bus가 app.state에 세팅되지 않는다.
        assert fake_pool.close_called
        assert not hasattr(test_app.state, "pool")
        assert not hasattr(test_app.state, "event_bus")


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


# ── Failure-injection test ──────────────────────────────────────────────────


async def test_lifespan_shutdown_clean_when_background_loops_raises() -> None:
    """실패주입: start_background_loops()가 예외를 raise하면 lifespan이
    예외를 전파하면서도 finally 블록에서 pool.close()를 호출하는지 확인한다.
    — I-01 (실패 시 정리 불변식).

    main.py lifespan()의 구조(pool 생성 직후부터 try로 감싼다 — task-5423
    이전에는 이 구간이 try 밖이라 여기서 실패하면 pool이 한 번도 close()되지
    않고 새는 회귀가 있었다):
        pool = await asyncpg.create_pool(...)   # 1) pool 생성
        try:
            ...
            await start_background_loops(...)    # 2) 루프 시작 (여기서 예외 발생)
            app.state.pool = pool                 # 3) 예외 때문에 도달하지 않음
            yield
        finally:
            await loops.stop()                    # loops가 None이면 skip
            await event_bus.stop()                # 4) finally가 무조건 실행
            await pool.close()                    # 5) pool 정리
    예외가 yield 전에 치명적이어도 finally는 실행되므로, pool이 close()되고
    app.state에는 부분 배선(pool/event_bus)이 등록되지 않음을 검증한다. pool
    자체는 실제 asyncpg pool을 만들지 않고 close() 호출 여부를 기록하는
    가짜 pool로 교체해, real DB 커넥션 없이도 정리 로직을 직접 관찰한다.
    """
    from fastapi import FastAPI

    from src.main import lifespan as _lifespan

    class _FakePool:
        def __init__(self) -> None:
            self.close_called = False

        async def close(self) -> None:
            self.close_called = True

        @property
        def _closed(self) -> bool:
            return self.close_called

    fake_pool = _FakePool()

    async def _fake_create_pool(*args: object, **kwargs: object) -> _FakePool:
        return fake_pool

    test_app = FastAPI(lifespan=_lifespan)

    def _fail_start(*args: object, **kwargs: object) -> None:
        raise RuntimeError("background_loops startup failed")

    with patch("src.main.asyncpg.create_pool", _fake_create_pool):
        with patch("src.main.start_background_loops", _fail_start):
            with pytest.raises(RuntimeError, match="background_loops startup failed"):
                async with test_app.router.lifespan_context(test_app):
                    pass
        # finally가 실행되었으므로 pool.close()가 실제로 호출되었다(회귀 시
        # False로 남아 이 assertion이 실패한다 — 게이트 적색 재현).
        assert fake_pool.close_called
        assert fake_pool._closed
        # 예외가 app.state.pool 대입(3번) 이전에 발생했으므로 부분 배선이
        # 남지 않는다.
        assert not hasattr(test_app.state, "pool")
        assert not hasattr(test_app.state, "event_bus")
