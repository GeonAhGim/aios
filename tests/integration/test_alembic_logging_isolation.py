"""Regression — in-process Alembic must not mute pre-existing application loggers.

`src/db/migrations/env.py`가 `logging.config.fileConfig()`를 기본값
(`disable_existing_loggers=True`)으로 호출하면, 그 시점에 이미 만들어진 모든
로거가 `disabled=True`가 된다. 테스트 스위트에서는 마이그레이션 왕복 테스트
(`alembic.command.upgrade/downgrade`)가 같은 xdist 워커에서 먼저 돌면 그 뒤의
caplog 기반 테스트가 실행 순서에 따라 빈 `caplog.records`를 보는 flaky가
됐다(CI main run #824: risk_decision_recorder clock_skew, background_loops,
compute_statement/start_validation observability 스냅샷).

두 테스트는 파일 순서대로 한 워커에서 돌며(`--dist loadfile`) 그 시나리오를
결정론적으로 재현한다: 첫 테스트가 Alembic을 프로세스 안에서 실행하고, 둘째
테스트가 그 전에 만들어진 로거로 caplog 캡처가 여전히 되는지 본다. 루트
핸들러는 `tests/conftest.py::_isolate_root_logger_state`가 테스트 사이에
복원하므로 여기서 검증하는 것은 `disabled` 플래그(env.py 수정 대상)다.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from alembic import command
from alembic.config import Config

_LOGGER_NAME = "src.tests.alembic_logging_isolation.probe"
_PROBE = logging.getLogger(_LOGGER_NAME)  # exists before Alembic runs


async def test_step1_in_process_alembic_leaves_existing_logger_enabled() -> None:
    assert _PROBE.disabled is False

    # `current` runs env.py (and therefore fileConfig) against the test DB
    # without changing the schema.
    await asyncio.to_thread(command.current, Config("alembic.ini"))

    assert _PROBE.disabled is False, "env.py fileConfig() muted a pre-existing logger"


def test_step2_caplog_still_captures_after_alembic_ran(caplog: pytest.LogCaptureFixture) -> None:
    assert _PROBE.disabled is False
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        _PROBE.info("probe_after_alembic")
    assert any(r.getMessage() == "probe_after_alembic" for r in caplog.records)
