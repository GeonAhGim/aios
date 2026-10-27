"""lifespan pool 생성 실패 롤백 — task-20211에서 test_app_lifespan.py 분리."""

from __future__ import annotations

from logging.handlers import QueueListener
from unittest.mock import patch

import asyncpg
import pytest


async def test_lifespan_rejects_pool_creation_failure() -> None:
    """실패주입: asyncpg.create_pool 이 raised하면 lifespan이 전체를
    롤백하고 app.state에 부분 상태를 남기지 않는다. — I-01(실패 닫힘).

    task-7633: create_pool 호출 자체가 try/finally 범위 안으로 옮겨졌으므로,
    이 실패 경로에서도 finally가 실행되어 log_listener.stop()이 호출됨을
    관측 가능하게 확인한다(회귀 시 QueueListener 스레드가 절대 멈추지 않는다).
    """
    import src.main as main_module

    def _fail_pool(*args: object, **kwargs: object) -> None:
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
    assert not hasattr(test_app.state, "pool")
    assert not hasattr(test_app.state, "event_bus")
