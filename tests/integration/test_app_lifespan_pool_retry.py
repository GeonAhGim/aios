"""src/main.py `_create_pool_with_retry` 단위테스트 — esc-ci-pytest/task-8259.

test_app_lifespan.py에서 분리(loc_over_500) — 원본 파일은 lifespan 전체
경로(실제 DB 연결)를 다루고, 여기는 lifespan의 pool 생성 재시도 로직만
mock으로 격리해 다룬다. 실제 DB 연결 불필요.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest


class _FakeAsyncpgError(OSError):
    """`OSError` 서브클래스 — `_create_pool_with_retry`가 잡는
    `_RETRYABLE_POOL_CONNECT_ERRORS`(OSError | ConnectionDoesNotExistError)
    중 OSError 경로를 실제 asyncpg 예외 타입 없이 재현한다."""


async def test_create_pool_with_retry_recovers_after_transient_reset() -> None:
    """실패주입(esc-ci-pytest/task-8259): 처음 두 번은 Windows TCP reset
    형태(`ConnectionDoesNotExistError`/`OSError`)로 실패하고 세 번째에
    성공하면, `_create_pool_with_retry`는 재시도 끝에 성공한 pool을
    돌려줘야 한다 — src/main.py의 실제 lifespan pool 생성 호출이 이
    transient 오류에서 죽지 않아야 한다는 회귀 재현."""
    import asyncpg

    import src.main as main_module

    attempts: list[int] = []
    fake_pool = object()
    terminated: list[object] = []

    class _FailingPool:
        def __init__(self, exc: BaseException) -> None:
            self._exc = exc

        def __await__(self) -> Generator[Any, None, object]:
            async def _raise() -> object:
                raise self._exc

            return _raise().__await__()

        def terminate(self) -> None:
            terminated.append(self)

    def _fake_create_pool(dsn: str, **kwargs: object) -> object:
        attempts.append(len(attempts))
        if len(attempts) <= 2:
            exc = (
                asyncpg.exceptions.ConnectionDoesNotExistError(
                    "connection was closed in the middle of operation"
                )
                if len(attempts) == 1
                else _FakeAsyncpgError("WinError 64")
            )
            return _FailingPool(exc)

        async def _succeed() -> object:
            return fake_pool

        return _succeed()

    async def _no_sleep(attempt: int) -> None:
        return None

    with (
        patch.object(main_module.asyncpg, "create_pool", _fake_create_pool),
        patch.object(main_module, "_sleep_before_pool_retry", _no_sleep),
    ):
        result = await main_module._create_pool_with_retry("postgresql://x")

    assert result is fake_pool
    assert len(attempts) == 3
    assert len(terminated) == 2


async def test_create_pool_with_retry_exhausts_attempts_and_raises() -> None:
    """실패주입: transient 오류가 `_POOL_CONNECT_ATTEMPTS`번 모두 재현되면
    fail-closed — 원래 예외가 그대로 전파돼야 하고(조용히 삼켜지지 않음),
    매 시도가 실패한 pool을 `terminate()`해 커넥션을 새지 않게 해야 한다."""
    import asyncpg

    import src.main as main_module

    attempts: list[int] = []
    terminated: list[object] = []

    class _AlwaysFailingPool:
        def __await__(self) -> Generator[Any, None, object]:
            async def _raise() -> object:
                raise asyncpg.exceptions.ConnectionDoesNotExistError(
                    "connection was closed in the middle of operation"
                )

            return _raise().__await__()

        def terminate(self) -> None:
            terminated.append(self)

    def _fake_create_pool(dsn: str, **kwargs: object) -> object:
        attempts.append(len(attempts))
        return _AlwaysFailingPool()

    async def _no_sleep(attempt: int) -> None:
        return None

    with (
        patch.object(main_module.asyncpg, "create_pool", _fake_create_pool),
        patch.object(main_module, "_sleep_before_pool_retry", _no_sleep),
    ):
        with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
            await main_module._create_pool_with_retry("postgresql://x")

    assert len(attempts) == main_module._POOL_CONNECT_ATTEMPTS
    assert len(terminated) == main_module._POOL_CONNECT_ATTEMPTS


async def test_create_pool_with_retry_does_not_retry_non_retryable_error() -> None:
    """`asyncpg.PostgresError`(자격증명 오류 등 비일시적 실패)는 재시도
    대상이 아니다 — 첫 시도에서 바로 전파돼 불필요한 지연 없이 fail-closed
    해야 한다(test_app_lifespan.py::test_lifespan_rejects_pool_creation_failure가
    lifespan 전체 경로로 같은 계약을 확인하는 것과 짝을 이루는 단위 테스트)."""
    import asyncpg

    import src.main as main_module

    attempts: list[int] = []

    def _fail_immediately(dsn: str, **kwargs: object) -> None:
        attempts.append(len(attempts))
        raise asyncpg.PostgresError("connection refused")

    with patch.object(main_module.asyncpg, "create_pool", _fail_immediately):
        with pytest.raises(asyncpg.PostgresError):
            await main_module._create_pool_with_retry("postgresql://x")

    assert len(attempts) == 1


def test_pool_retry_delay_grows_exponentially_and_caps() -> None:
    """성능/수치 단언(D2): 백오프 지연이 매 시도 2배로 늘다가
    `_POOL_CONNECT_RETRY_MAX_DELAY`에서 멈춰야 한다 — 무한정 늘어나
    재시도 예산(`_POOL_CONNECT_ATTEMPTS`)을 벽시계 시간으로 무력화하지
    않는지 확인한다."""
    import src.main as main_module

    base = main_module._POOL_CONNECT_RETRY_BASE_DELAY
    cap = main_module._POOL_CONNECT_RETRY_MAX_DELAY
    assert main_module._pool_retry_delay(0) == base
    assert main_module._pool_retry_delay(1) == base * 2
    assert main_module._pool_retry_delay(2) == base * 4
    # 충분히 큰 attempt는 지수 성장이 아니라 cap에서 멈춘다.
    assert main_module._pool_retry_delay(10) == cap
