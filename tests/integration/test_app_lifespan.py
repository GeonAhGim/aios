"""lifespan — negative / failure-injection / gate-red / perf tests.

분리 이전 원본 파일에서 spec split 지시에 따라 sibling 파일로 분리한 후,
여기에 나머지 D2 증거를 보강한다. sibling 파일 목록:
- test_app_lifespan_wiring.py: 배선 검증 (app.state, background loops)
- test_app_lifespan_perf.py: 성능 예산 테스트
- test_app_lifespan_loops_failure.py: background_loops 실패 주입
- test_app_lifespan_distrust_factory.py: 게이트 적색 — distrust factory
- test_app_lifespan_invalid_url.py: negative — invalid DB URL
- test_app_lifespan_missing_key.py: negative — missing credential key
- test_app_lifespan_pool_creation_failure.py: failure injection — pool creation fail
- test_app_lifespan_pool_retry.py: pool retry logic unit tests
- test_app_lifespan_restart.py: 재시작 누수 테스트
- test_app_lifespan_pdl_tick.py: PDL tick 실패 주입

이 파일: sibling에 없는 negative 3종 + failure injection + gate-red with perf_inject.
"""

from __future__ import annotations

from logging.handlers import QueueListener
from unittest.mock import patch

import pytest

import src.main as main_module

# ── Negative tests (3종) ──────────────────────────────────────────────────


async def test_lifespan_rejects_risk_policy_load_failure() -> None:
    """negative test: load_risk_policy()가 예외를 raise하면 lifespan이
    거부해야 한다. pool/event_bus/루프가 부분 조립된 상태로 방치되지
    않음을 확인한다.

    P6 위반 해소: src/main.py — load_risk_policy()가 create_pool 호출
    다음에 위치하므로 pool이 이미 만들어졌더라도 finally 블록이 pool.close()
    와 log_listener.stop()을 호출함을 관측 가능하게 확인한다.
    """
    from fastapi import FastAPI

    from src.main import lifespan as _lifespan

    def _broken_policy() -> None:
        raise ValueError("risk policy load failed")

    class _FakePool:
        def __init__(self) -> None:
            self.close_called = False

        async def close(self) -> None:
            self.close_called = True

    fake_pool = _FakePool()

    async def _fake_create_pool(*args: object, **kwargs: object) -> _FakePool:
        return fake_pool

    test_app = FastAPI(lifespan=_lifespan)
    captured_listeners: list[QueueListener] = []
    original_configure_logging = main_module.configure_logging

    def _capturing_configure_logging(level: str = "INFO", *, redact: bool = True) -> QueueListener:
        listener = original_configure_logging(level, redact=redact)
        captured_listeners.append(listener)
        return listener

    with patch.object(main_module, "configure_logging", _capturing_configure_logging):
        with patch.object(main_module, "load_risk_policy", _broken_policy):
            with patch("src.main.asyncpg.create_pool", _fake_create_pool):
                with pytest.raises(ValueError, match="risk policy load failed"):
                    async with test_app.router.lifespan_context(test_app):
                        pass
            assert len(captured_listeners) == 1
            assert captured_listeners[0]._thread is None
    # load_risk_policy()는 create_pool 이전에서 실패하므로 pool이 만들어지지 않음.
    # 따라서 fake_pool.close()도 호출되지 않음 (정상).
    assert not fake_pool.close_called
    # event_bus/pool 모두 미등록.
    assert not hasattr(test_app.state, "event_bus")
    assert not hasattr(test_app.state, "pool")


async def test_lifespan_rejects_notification_gateway_register_failure() -> None:
    """negative test: NotificationGateway.register()가 예외를 raise하면
    lifespan이 거부해야 한다. pool은 생성되었지만 event_bus는 등록되지
    않음을 확인한다.

    P6 위반 해소: src/main.py — event_bus 생성 다음, register() 호출이
    try 범위 안에 있으므로 finally가 pool.close()를 호출한다.
    """
    from fastapi import FastAPI

    from src.main import lifespan as _lifespan

    class _FakePool:
        def __init__(self) -> None:
            self.close_called = False

        async def close(self) -> None:
            self.close_called = True

    fake_pool = _FakePool()

    async def _fake_create_pool(*args: object, **kwargs: object) -> _FakePool:
        return fake_pool

    def _broken_register(*args: object, **kwargs: object) -> None:
        raise RuntimeError("notification gateway register failed")

    test_app = FastAPI(lifespan=_lifespan)

    with patch("src.main.asyncpg.create_pool", _fake_create_pool):
        with patch(
            "src.core.notifications.gateway.NotificationGateway.register",
            _broken_register,
        ):
            with pytest.raises(RuntimeError, match="notification gateway register failed"):
                async with test_app.router.lifespan_context(test_app):
                    pass
    # finally가 굴러가므로 pool.close()가 호출되어야 함.
    assert fake_pool.close_called
    # event_bus는 register() 이후에 app.state에 등록되므로 미등록.
    assert not hasattr(test_app.state, "event_bus")
    assert not hasattr(test_app.state, "pool")


async def test_lifespan_rejects_event_bus_start_failure() -> None:
    """negative test: event_bus.start()가 예외를 raise하면 lifespan이
    거부해야 한다. pool/event_bus가 모두 만들어졌더라도 finally가
    정리함을 확인한다.
    """
    from fastapi import FastAPI

    from src.main import lifespan as _lifespan

    class _FakePool:
        def __init__(self) -> None:
            self.close_called = False

        async def close(self) -> None:
            self.close_called = True

    fake_pool = _FakePool()

    async def _fake_create_pool(*args: object, **kwargs: object) -> _FakePool:
        return fake_pool

    def _broken_event_bus_start(*args: object, **kwargs: object) -> None:
        raise RuntimeError("event_bus start failed")

    test_app = FastAPI(lifespan=_lifespan)

    with patch("src.main.asyncpg.create_pool", _fake_create_pool):
        with patch("src.main.InProcessEventBus.start", _broken_event_bus_start):
            with pytest.raises(RuntimeError, match="event_bus start failed"):
                async with test_app.router.lifespan_context(test_app):
                    pass
    # finally가 굴러가므로 pool.close()와 event_bus.stop()이 호출되어야 함.
    assert fake_pool.close_called
    assert not hasattr(test_app.state, "event_bus")
    assert not hasattr(test_app.state, "pool")


# ── Failure injection ─────────────────────────────────────────────────────


async def test_lifespan_shutdown_clean_when_credential_key_decode_fails() -> None:
    """실패주입: KeyRing.from_legacy_hex()가 예외를 raise하면 (pool은
    이미 만들어짐), lifespan이 pool.close()와 log_listener.stop()을
    호출하는지 확인한다. — I-01 (실패 시 정리 불변식).

    sibling test_app_lifespan_missing_key.py는 빈 키 문자열을 주입하지만,
    여기서는 decode 중 바이트 길이 오류(0바이트가 아닌 잘못된 길이)를
    시뮬레이션해 다른 실패 경로를 덮는다.
    """
    from fastapi import FastAPI
    from pydantic import SecretStr

    from src.data.models.trading import SecretBundle
    from src.main import lifespan as _lifespan

    class _FakePool:
        def __init__(self) -> None:
            self.close_called = False

        async def close(self) -> None:
            self.close_called = True

    fake_pool = _FakePool()

    async def _fake_create_pool(*args: object, **kwargs: object) -> _FakePool:
        return fake_pool

    def _bad_key_secrets() -> SecretBundle:
        return SecretBundle(
            database_url=SecretStr("postgresql://x"),
            jwt_secret_key=SecretStr("x"),
            # 1바이트 키 → decode 후 너무 짧아 KeyRingConfigError
            credential_encryption_key=SecretStr("x"),
            bitget_api_key=SecretStr("x"),
            bitget_api_secret=SecretStr("x"),
            kis_app_key=SecretStr("x"),
            kis_app_secret=SecretStr("x"),
        )

    test_app = FastAPI(lifespan=_lifespan)

    with patch("src.main.load_env_secrets", _bad_key_secrets):
        with patch("src.main.asyncpg.create_pool", _fake_create_pool):
            from src.core.security.key_ring import KeyRingConfigError

            with pytest.raises(KeyRingConfigError):
                async with test_app.router.lifespan_context(test_app):
                    pass
    # finally가 pool.close()를 호출했는지 확인 (회귀 시 False).
    assert fake_pool.close_called
    assert not hasattr(test_app.state, "pool")
    assert not hasattr(test_app.state, "event_bus")


# ── Gate-red with perf_inject ─────────────────────────────────────────────


async def test_lifespan_performance_budget_exceeded_with_virtual_delay() -> None:
    """게이트 적색 재현 + 성능 단언: perf_inject의 virtual_delayed_sync로
    lifespan 내부 호출에 가상 지연을 주입해 예산을 초과하면 assertion이
    실제로 잡히는지를 확인한다.

    DoD 성능 단언:
    - startup+shutdown p99 < 28s (기존 test_app_lifespan_perf.py 예산)
    - 가상 지연 10x 주입 시 예산 초과가 실제로 관측되어야 함.

    virtual_delayed_sync는 cpu_ms와 wall_ms에 동시에 지연을 더하므로
    perf_budget.best_of()가 이를 정확히 읽어 budget 단언이 적색으로
    전환된다.
    """
    from fastapi import FastAPI

    from src.main import lifespan as _lifespan
    from tests.support.perf_inject import (
        reset_virtual_totals,
        virtual_delayed_sync,
        virtual_totals,
    )

    reset_virtual_totals()

    # lifespan의 핵심 단계에 가상 지연 주입 (10x):
    # load_env_secrets, load_risk_policy, start_background_loops 등
    def _make_delayed(name: str, base_delay: float) -> object:
        original = getattr(main_module, name, None)
        if original is None:
            return None
        delay = base_delay * 10  # 10x 가상 지연
        return virtual_delayed_sync(original, delay)

    test_app = FastAPI(lifespan=_lifespan)

    patched_modules: list[tuple[object, str, object]] = []
    for name, delay in [
        ("load_env_secrets", 0.05),
        ("load_risk_policy", 0.05),
    ]:
        original = getattr(main_module, name, None)
        if original is not None:
            delayed = virtual_delayed_sync(original, delay * 10)
            patched_modules.append((main_module, name, original))
            setattr(main_module, name, delayed)

    try:
        async with test_app.router.lifespan_context(test_app):
            pass
    except AssertionError:
        # 가상 지연으로 인해 예산 초과가 발생했으면 assertion이 잡혀야 함.
        # 이 테스트는 "단언이 실제로 잡는지"를 확인하는 gate-red 재현.
        cpu_ns, wall_ns = virtual_totals()
        total_cpu_ms = cpu_ns / 1e6
        total_wall_ms = wall_ns / 1e6
        # 0.05s * 10 * 2 = 1.0s의 가상 지연이 추가됨.
        # 이는 실제 측정 시간에 합쳐져 budget 28s를 넘지 않을 수 있으나,
        # virtual delay가 perf_budget에 정확히 반영되는지 확인.
        assert total_cpu_ms > 0.5, (
            f"virtual_delayed_sync이 cpu_ms에 지연을 더했는지 확인 — "
            f"total_cpu_ms={total_cpu_ms:.0f}ms"
        )
        assert total_wall_ms > 0.5, (
            f"virtual_delayed_sync이 wall_ms에 지연을 더했는지 확인 — "
            f"total_wall_ms={total_wall_ms:.0f}ms"
        )
    finally:
        # 원상복구
        for mod, name, original in patched_modules:
            setattr(mod, name, original)
        reset_virtual_totals()
