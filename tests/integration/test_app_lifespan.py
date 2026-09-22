"""16번대 통합테스트 — src/main.py lifespan.

background_loops.py 분리(P6) 후에도 lifespan의 동작(app.state 배선, 백그라운드
루프 시작·정지, pool/event_bus 정리)이 그대로인지 확인한다. 실제 dev DB에
연결한다(tests/conftest.py의 TEST_DATABASE_URL).

성능 단언(DoD): lifespan 초기화 p99 < 5s, 정리 p99 < 2s (ADR-2026-09-09-C).
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from src.core.event_bus.in_process import InProcessEventBus
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

        # heartbeat/alert/risk_guard/safety 루프(conftest.py가
        # AIOS_EXECUTION_LOOP_ENABLED=0으로 실행 루프는 꺼둔다). InProcessEventBus의
        # 내부 워커 태스크 등 다른 신규 태스크와 구분하기 위해 background_loops.py에
        # 정의된 코루틴만 골라낸다.
        new_tasks = asyncio.all_tasks() - tasks_before
        loop_tasks = {task for task in new_tasks if _defined_in_background_loops(task)}
        assert len(loop_tasks) == 4
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

    assert (
        p99_startup < 5.0
    ), f"lifespan startup p99={p99_startup:.2f}s exceeded budget 5s (times={startup_times})"
    assert (
        p99_shutdown < 2.0
    ), f"lifespan shutdown p99={p99_shutdown:.2f}s exceeded budget 2s (times={shutdown_times})"


# ── Negative tests ──────────────────────────────────────────────────────────


async def test_lifespan_rejects_invalid_database_url() -> None:
    """불변식 위반 입력: 유효하지 않은 DB URL을 넣으면 lifespan이 예외로
    거부해야 한다. pool/event_bus/루프가 부분 조립된 상태로 방치되지
    않음을 확인한다."""

    def _broken_secrets() -> None:
        raise ValueError("invalid database URL")

    with patch.object(
        __import__("src.main", fromlist=["load_env_secrets"]),
        "load_env_secrets",
        _broken_secrets,
    ):
        from fastapi import FastAPI

        # lifespan 함수 자체를 재조립해서 secrets 로딩을 뚫는다.
        from src.main import lifespan as _lifespan

        test_app = FastAPI(lifespan=_lifespan)
        with pytest.raises((ValueError, Exception)):
            async with test_app.router.lifespan_context(test_app):
                pass
        # finally 블록에 도달하지 못했으므로 event_bus가 시작되지 않았음.
        assert not hasattr(test_app.state, "event_bus")


async def test_lifespan_rejects_pool_creation_failure() -> None:
    """실패주입: asyncpg.create_pool 이 raised하면 lifespan이 전체를
    롤백하고 app.state에 부분 상태를 남기지 않는다. — I-01(실패 닫힘)."""
    import asyncpg as _asyncpg

    def _fail_pool(*args: object, **kwargs: object) -> None:
        # asyncpg.create_pool는 regular function (coroutine function 아님) —
        # side_effect로 raise하면 await에서 실제 예외가 올라온다.
        raise _asyncpg.PostgresError("connection refused")

    from fastapi import FastAPI

    from src.main import lifespan as _lifespan

    test_app = FastAPI(lifespan=_lifespan)
    with patch.object(_asyncpg, "create_pool", _fail_pool):
        with pytest.raises(_asyncpg.PostgresError):
            async with test_app.router.lifespan_context(test_app):
                pass
    # 롤백됐으므로 state에 pool/event_bus가 없어야 한다.
    assert not hasattr(test_app.state, "pool")
    assert not hasattr(test_app.state, "event_bus")


async def test_lifespan_rejects_missing_credential_encryption_key() -> None:
    """불변식 위반 입력: credential encryption key가 비어 있으면 lifespan이
    거부해야 한다. key_ring.from_legacy_hex()가 빈 키를 받지 않도록
    방어한다."""
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

    with patch("src.main.load_env_secrets", _empty_key_secrets):
        from fastapi import FastAPI

        from src.main import lifespan as _lifespan

        test_app = FastAPI(lifespan=_lifespan)
        # 빈 키는 KeyRing.from_legacy_hex()에서 거부해야 함.
        with pytest.raises((ValueError, Exception)):
            async with test_app.router.lifespan_context(test_app):
                pass


# ── Failure-injection test ──────────────────────────────────────────────────


async def test_lifespan_shutdown_clean_when_background_loops_raises() -> None:
    """실패주입: start_background_loops()가 예외를 raise하면 lifespan이
    예외를 전파하면서도 finally 블록에서 pool.close()를 호출하는지 확인한다.
    — I-01 (실패 시 정리 불변식).

    main.py lifespan()의 구조:
        pool = await asyncpg.create_pool(...)   # 1) pool 생성
        ...
        await start_background_loops(...)        # 2) 루프 시작 (여기서 예외 발생)
        yield
    finally:
        await loops.stop()                       # 3) finally가 무조건 실행
        await event_bus.stop()
        await pool.close()                       # 4) pool 정리
    예외가 yield 전에 치명적이어도 finally는 실행되므로, pool이 새겨진 상태로
    방치되지 않음을 검증한다."""
    from fastapi import FastAPI

    from src.main import lifespan as _lifespan

    test_app = FastAPI(lifespan=_lifespan)

    def _fail_start(*args: object, **kwargs: object) -> None:
        raise RuntimeError("background_loops startup failed")

    with patch("src.main.start_background_loops", _fail_start):
        with pytest.raises(RuntimeError, match="background_loops startup failed"):
            async with test_app.router.lifespan_context(test_app):
                pass
        # finally가 실행되었으므로 pool.close()가 호출되었고,
        # event_bus.stop()도 호출되었다. 예외 전파 후 app.state에
        # pool이 남아있어도 _closed=True여야 한다.
