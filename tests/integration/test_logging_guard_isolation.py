"""Negative — the `_isolate_root_logger_state` autouse guard must undo a test
that forgets to reset process-global logging state.

`logging.disable(level)` is not a per-logger attribute; it sets
`logging.Logger.manager.disable`, a single process-wide integer that gates
every logger regardless of its own level/handlers. A test that calls it and
never calls `logging.disable(logging.NOTSET)` would otherwise silence
`caplog` for every test that runs afterward in the same xdist worker
(task-7439). These two tests run in file order (`--dist loadfile` keeps a
file on one worker) to prove the guard resets the leak between them, the same
pattern as `test_alembic_logging_isolation.py`.
"""

from __future__ import annotations

import logging

import pytest


def test_step1_fake_test_leaves_logging_disabled_globally() -> None:
    """Simulates the bug: a test calls `logging.disable()` and never restores it."""
    assert logging.Logger.manager.disable == logging.NOTSET
    logging.disable(logging.CRITICAL)
    assert logging.Logger.manager.disable == logging.CRITICAL


def test_step2_guard_restores_global_disable_for_next_test(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If the guard did not restore `manager.disable`, this caplog assertion
    would see zero records regardless of level -- the exact flake this leaf
    fixes."""
    assert logging.Logger.manager.disable == logging.NOTSET, (
        "logging.disable() leaked across tests -- _isolate_root_logger_state regressed"
    )
    logger = logging.getLogger("tests.logging_guard_isolation.probe")
    with caplog.at_level(logging.INFO, logger=logger.name):
        logger.info("probe_after_leak")
    assert any(r.getMessage() == "probe_after_leak" for r in caplog.records)
