"""DEEPEN(task-7721) for tests/adversarial/execution_ownership/conftest.py.

`conftest.py` only re-exports `pool` from
`tests.integration.foundation.execution_ownership.conftest` (no new logic --
task-6704 origin leaf, "고아 산출물 회수"). The re-export itself has 0
negative tests and no failure-injection coverage (task-4084 DEEPEN
baseline). This file targets the two pieces of real logic behind that
re-export -- `_asyncpg_dsn()` (DSN scheme rewrite) and the `pool` fixture
body (`create_pool_with_retry` call + teardown `close()`) -- without
touching either conftest.py (30403721/task-7723 conftest DEEPEN pattern).
"""

from __future__ import annotations

import os
from typing import Any, cast

import asyncpg
import pytest

import tests.integration.foundation.execution_ownership.conftest as _origin_conftest
from tests.integration.foundation.execution_ownership.conftest import _asyncpg_dsn, pool

_pool_fn: Any = cast(Any, pool).__wrapped__

# ---------------------------------------------------------------------------
# Negative tests -- _asyncpg_dsn must fail closed / must not silently repair
# invariant-violating input.
# ---------------------------------------------------------------------------


class TestAsyncpgDsnNegative:
    def test_raises_when_database_url_is_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No silent default DSN -- a missing `DATABASE_URL` must fail closed
        with `KeyError`, not fall back to a hardcoded/local connection string."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(KeyError):
            _asyncpg_dsn()

    def test_does_not_rewrite_a_scheme_it_does_not_recognize(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`_asyncpg_dsn` is a literal `str.replace`, not a URL parser -- a
        scheme it doesn't special-case (e.g. a `postgres+asyncpg://` typo)
        must pass through unchanged rather than being "helpfully" coerced.
        Regression would hide a broken env var behind a plausible-looking
        DSN instead of failing the connection loudly."""
        monkeypatch.setenv("DATABASE_URL", "postgres+asyncpg://user:pw@host/db")
        assert _asyncpg_dsn() == "postgres+asyncpg://user:pw@host/db"

    def test_empty_database_url_yields_empty_dsn_not_a_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty `DATABASE_URL` must produce an empty DSN (which will
        fail loudly downstream at `create_pool_with_retry`), never a
        silently substituted default connection target."""
        monkeypatch.setenv("DATABASE_URL", "")
        assert _asyncpg_dsn() == ""

    def test_rewrites_every_occurrence_of_the_full_scheme_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`str.replace` with no count rewrites every occurrence of the
        exact `postgresql+asyncpg://` marker, not just a leading prefix --
        pin this down so a future switch to a proper URL parser (which
        would only touch the scheme) is a deliberate decision, not an
        accidental behavior change."""
        dsn = "postgresql+asyncpg://user:pw@host/db?opt=postgresql+asyncpg://nested"
        monkeypatch.setenv("DATABASE_URL", dsn)
        assert _asyncpg_dsn() == "postgresql://user:pw@host/db?opt=postgresql://nested"


# ---------------------------------------------------------------------------
# Failure injection -- the pool fixture body must propagate connection
# failures rather than swallowing them.
# ---------------------------------------------------------------------------


class TestPoolFixtureFailureInjection:
    async def test_create_pool_with_retry_failure_propagates_fail_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If `create_pool_with_retry` exhausts its bounded retries (DB down,
        bad DSN, auth rejected), the `pool` fixture must let the exception
        propagate to the test -- it must not swallow the error and hand
        back a broken/None pool that later tests would misinterpret as
        "no data" instead of "connection failed"."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pw@host/db")

        async def _boom(*_args: Any, **_kwargs: Any) -> asyncpg.Pool[asyncpg.Connection]:
            raise OSError("connection refused")

        monkeypatch.setattr(_origin_conftest, "create_pool_with_retry", _boom)

        agen = _pool_fn()
        with pytest.raises(OSError, match="connection refused"):
            await agen.__anext__()

    async def test_pool_is_closed_on_teardown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The fixture must call `.close()` on teardown so connections are
        not leaked across tests -- a regression that drops the `yield`
        cleanup would exhaust the pool's `max_size=8` connection budget
        over a long-running suite."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pw@host/db")
        closed = {"called": False}

        class _FakePool:
            async def close(self) -> None:
                closed["called"] = True

        async def _fake_create_pool_with_retry(*_args: Any, **_kwargs: Any) -> _FakePool:
            return _FakePool()

        monkeypatch.setattr(
            _origin_conftest, "create_pool_with_retry", _fake_create_pool_with_retry
        )

        agen = _pool_fn()
        yielded = await agen.__anext__()
        assert isinstance(yielded, _FakePool)
        assert closed["called"] is False

        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()
        assert closed["called"] is True


# ---------------------------------------------------------------------------
# Gate-red reproduction -- prove the negative test actually catches a
# regression, not just a tautology.
# ---------------------------------------------------------------------------


def test_gate_red_dsn_scheme_rewrite_regression_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulates the regression a naive "fix" would introduce: rewriting
    the scheme unconditionally to `postgresql://` regardless of input,
    which would silently mask a malformed `DATABASE_URL` (e.g. missing the
    `+asyncpg` marker entirely) instead of leaving it visibly broken."""

    def _regressed_dsn() -> str:
        return "postgresql://" + os.environ["DATABASE_URL"].split("://", 1)[-1]

    monkeypatch.setenv("DATABASE_URL", "sqlite:///not-a-postgres-db")
    correct = _asyncpg_dsn()
    regressed = _regressed_dsn()

    assert correct == "sqlite:///not-a-postgres-db"
    assert regressed != correct, (
        "regression would have silently coerced a non-postgres DSN into a "
        "postgresql:// one -- this assertion must fail if that regression lands"
    )
