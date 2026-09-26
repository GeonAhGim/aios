"""FA-14 통합테스트 공용 픽스처.

`tests/conftest.py`가 `TEST_DATABASE_URL`을 `DATABASE_URL` 환경변수로
옮겨 두므로, 여기서는 그 값을 asyncpg DSN으로 변환한 `pool` 픽스처만 둔다
(다른 통합테스트 디렉터리들과 동일 관례, 예: `tests/integration/foundation/
ledger/conftest.py`).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import timedelta

import asyncpg
import pytest

from tests.integration.eventstore._replay_verify_support import (
    _clock,
    assert_no_replay_window_leftovers,
)
from tests.support.db import (
    TEMPLATE_DATABASE_URL_ENV,
    drop_worker_database,
    ensure_worker_database,
    template_database_url,
)
from tests.support.db import (
    _asyncpg_dsn as _clone_dsn,
)


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


# `session_database_url` caps the composed clone name at 40 chars, and this
# suffix stacks with `_REPLAY_CLONE_SUFFIX` (e.g. `..._p_master_rv_tamper`), so
# it stays a single character to leave room for longer per-worktree base names
# (e.g. `aios_test_backend_1`) plus the longest registered replay suffix.
_PRISTINE_CLONE_WORKER_ID = "p"


def _pristine_replay_template_url() -> str:
    """Untouched template for `isolated_replay_db_url`, valid under both xdist
    and master (serial, `-n`-less) runs.

    Under xdist, `tests/conftest.py` sets `AIOS_TEST_TEMPLATE_DATABASE_URL` to
    the pre-swap `TEST_DATABASE_URL` before any worker mutates its own clone,
    so `template_database_url()` alone is safe there. Under master mode (the
    `.github/workflows/quality.yml` "Test (perf, serial)" step / local_ci
    `pytest_perf` stage), that env var is never set (`tests/conftest.py`'s
    `_WORKER_ID != "master"` guard) and `template_database_url()` falls back to
    the live `DATABASE_URL` -- the same connection every other perf test ahead
    of this module in collection order keeps writing `order_events`/ledger rows
    to (esc-ci-pytest_perf: `assert_no_replay_window_leftovers` found 5000+
    recent rows there). Clone once here, at *module import time*: pytest fully
    collects every test (importing every conftest.py along the way) before
    executing any of them, so this capture happens before the first perf test
    runs -- the same ordering guarantee `tests/conftest.py` itself relies on
    for its per-worker clone.
    """
    inherited = os.environ.get(TEMPLATE_DATABASE_URL_ENV)
    if inherited:
        return inherited
    return asyncio.run(ensure_worker_database(template_database_url(), _PRISTINE_CLONE_WORKER_ID))


_PRISTINE_REPLAY_TEMPLATE_URL = _pristine_replay_template_url()


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=16)
    yield p
    await p.close()


# `session_database_url` caps worker DB names at 40 chars (e.g.
# `aios_test_cloud_master_<suffix>` locally), so each replay module gets a short
# registered suffix instead of its full module name.
_REPLAY_CLONE_SUFFIX: dict[str, str] = {
    "test_replay_verify": "rv",
    "test_replay_verify_order_chain": "rv_chain",
    "test_replay_verify_tamper_detection": "rv_tamper",
}


def _replay_clone_id(request: pytest.FixtureRequest) -> str:
    """Deterministic, short clone name per (xdist worker, replay test module):
    `gw0_rv`, `gw0_rv_chain`, `gw0_rv_tamper`."""
    worker = os.environ.get("PYTEST_XDIST_WORKER", "master")
    module = request.module.__name__.rsplit(".", 1)[-1]
    suffix = _REPLAY_CLONE_SUFFIX.get(module)
    if suffix is None:
        raise ValueError(f"no replay clone suffix registered for module {module!r}")
    return f"{worker}_{suffix}"


@pytest.fixture
async def isolated_replay_db_url(request: pytest.FixtureRequest) -> AsyncIterator[str]:
    """Per-test database cloned from the untouched template for the FA-15
    replay_verify modules, dropped again at teardown.

    `replay_verify.verify(hours=1)` (and the `scripts/replay_verify.py`
    subprocess) digests *every* order/ledger stream touched inside the window,
    so the shared xdist worker DB is the wrong place for these assertions: a
    row another module left behind (an order whose status moved outside the
    event trail, a PLATFORM ledger posting) shows up as a StreamDiff on a key
    this module never created (PR #91 runs 36222175795, 36224833535,
    36225868874; same pattern as oms/test_cancel_requested_replay.py fe50dcd4).
    The template URL (not the live worker DB) avoids ObjectInUseError while
    other sessions are open. Before yielding, the window is asserted clean so a
    dirty template is reported at the source with sample keys instead of as a
    StreamDiff later."""
    clone_id = _replay_clone_id(request)
    template = _PRISTINE_REPLAY_TEMPLATE_URL
    url = await ensure_worker_database(template, clone_id)
    try:
        guard_pool = await asyncpg.create_pool(_clone_dsn(url), min_size=1, max_size=2)
        try:
            await assert_no_replay_window_leftovers(
                guard_pool, as_of=_clock() + timedelta(minutes=1), hours=1
            )
        finally:
            await guard_pool.close()
        yield url
    finally:
        await drop_worker_database(template, clone_id)


@pytest.fixture
async def isolated_replay_pool(isolated_replay_db_url: str) -> AsyncIterator[asyncpg.Pool]:
    p = await asyncpg.create_pool(_clone_dsn(isolated_replay_db_url), min_size=1, max_size=8)
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture(autouse=True)
async def _ledger_control_clean_slate(pool):
    """`tests/integration/foundation/ledger/conftest.py`의 동명 픽스처와
    같은 이유(다른 디렉터리의 LC-10 테스트가 남긴 `write_frozen` 잔류가
    이 디렉터리의 원장 투영 테스트를 영구히 막지 않도록)."""

    async def _reset() -> None:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE ledger_control SET write_frozen = FALSE, frozen_reason = NULL, "
                "frozen_at = NULL WHERE id = 1"
            )

    await _reset()
    yield
    await _reset()
