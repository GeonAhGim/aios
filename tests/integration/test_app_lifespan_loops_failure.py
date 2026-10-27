"""src/main.py lifespan — background_loops 실패 주입 테스트.

test_app_lifespan.py에서 분리 — start_background_loops() 실패 시 pool/event_bus
정리 불변식(I-01)만 격리해 다룬다. 실제 DB 연결 불필요.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


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
