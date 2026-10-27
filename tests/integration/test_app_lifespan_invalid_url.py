"""lifespan — invalid database URL rejection.

분리 근거: test_app_lifespan.py의 test_lifespan_rejects_invalid_database_url
함수를 spec split 지시에 따라 sibling 파일로 분리함.
"""

from __future__ import annotations

from logging.handlers import QueueListener
from unittest.mock import patch

import pytest

import src.main as main_module


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
    from fastapi import FastAPI

    from src.main import lifespan as _lifespan

    def _broken_secrets() -> None:
        raise ValueError("invalid database URL")

    with patch.object(main_module, "load_env_secrets", _broken_secrets):
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
