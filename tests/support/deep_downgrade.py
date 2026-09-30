"""Purge position snapshots before a migration round trip that descends below FA-4.

FA-0d-fix (task-771991202). `cdb114b6903f` (FA-0d) rewrites `pos_snapshot`
keys 5 -> 4 parts on downgrade and rebuilds the 5th part from the
`portfolio_id` column on upgrade, failing closed (correctly) on any row it
cannot re-key. The snapshot adapter now always fills that column, so a round
trip that stays above FA-4 (`963d5f3cfb1b`) is lossless. A deeper downgrade
is not: FA-4's downgrade drops the column, and FA-2's downgrade drops the
`legal_entity`/`fund`/`portfolio` tables themselves, so on the way back up
the FA-4 backfill has nothing to derive `portfolio_id` from and FA-0d halts
-- leaving the shared test database stuck below head and every later test
failing (the CI red behind 77871f67).

Such a deep downgrade already discards all entity rows in the test database;
discarding the position snapshots that depend on them is the same, explicit
choice. Call `purge_position_snapshots` right before the downgrade in every
test whose target is below FA-4. Nothing references `pos_snapshot` by FK, and
`pos_journal` (WORM, append-only) is untouched.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest


async def purge_position_snapshots(pool: asyncpg.Pool) -> int:
    """Delete every `pos_snapshot` row; returns how many were removed."""
    async with pool.acquire() as conn:
        status = await conn.execute("DELETE FROM pos_snapshot")
    return int(status.rsplit(" ", 1)[-1])


# ---------------------------------------------------------------------------
# Negative tests (3+) — invariant-violating inputs are explicitly rejected
# ---------------------------------------------------------------------------


class TestPurgePositionSnapshotsNegative:
    """Negative tests: invalid pool types, empty result, non-pool objects."""

    async def test_rejects_none_pool(self) -> None:
        """purge_position_snapshots must raise when pool is None."""
        with pytest.raises((AttributeError, TypeError)):
            await purge_position_snapshots(None)  # type: ignore[arg-type]

    async def test_rejects_sync_pool_mock(self) -> None:
        """A plain MagicMock (not asyncpg.Pool) must not silently succeed."""
        fake_pool = MagicMock()
        # MagicMock.__aenter__ returns a sync context manager by default,
        # but asyncpg.Pool.acquire() returns an async context manager.
        # Passing a non-async pool should raise AttributeError or TypeError.
        with pytest.raises((AttributeError, TypeError, ValueError)):
            await purge_position_snapshots(fake_pool)  # type: ignore[arg-type]

    async def test_rejects_non_context_manager_pool(self) -> None:
        """Pool.acquire() returning a non-context-manager must fail."""
        fake_pool = MagicMock()
        fake_pool.acquire.return_value = "not_a_context_manager"
        with pytest.raises((TypeError, AttributeError)):
            await purge_position_snapshots(fake_pool)  # type: ignore[arg-type]

    async def test_returns_zero_when_table_empty(self) -> None:
        """When pos_snapshot has no rows, return 0 (not an error)."""
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "DELETE 0"
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=mock_cm)

        result = await purge_position_snapshots(mock_pool)  # type: ignore[arg-type]

        assert result == 0
        mock_conn.execute.assert_called_once_with("DELETE FROM pos_snapshot")


# ---------------------------------------------------------------------------
# Failure-injection tests (1+) — monkeypatch dependency exceptions
# ---------------------------------------------------------------------------


class TestPurgePositionSnapshotsFailureInjection:
    """Failure injection: asyncpg raises during DELETE."""

    async def test_asyncpg_connection_error_propagates(self) -> None:
        """asyncpg.Connection.execute() raising asyncpg.PostgresError must propagate."""
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = asyncpg.PostgresError("connection refused")
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=mock_cm)

        with pytest.raises(asyncpg.PostgresError, match="connection refused"):
            await purge_position_snapshots(mock_pool)  # type: ignore[arg-type]

    async def test_asyncpg_foreign_key_violation_propagates(self) -> None:
        """ForeignKeyViolation must propagate — we do not swallow DB errors."""
        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = asyncpg.ForeignKeyViolationError(
            "fk_pos_snapshot_ref", "pos_snapshot"
        )
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=mock_cm)

        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await purge_position_snapshots(mock_pool)  # type: ignore[arg-type]

    async def test_pool_acquire_raises_on_bootstrap_failure(self) -> None:
        """Pool.acquire() failing during DB bootstrap must propagate."""
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(side_effect=asyncpg.PostgresError("pool exhausted"))
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=mock_cm)

        with pytest.raises(asyncpg.PostgresError, match="pool exhausted"):
            await purge_position_snapshots(mock_pool)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Positive smoke test — valid row count is returned correctly
# ---------------------------------------------------------------------------


class TestPurgePositionSnapshotsPositive:
    """Positive smoke: valid row counts are parsed from asyncpg status strings."""

    async def test_returns_delete_count_from_status_string(self) -> None:
        """asyncpg returns 'DELETE N'; we must parse N correctly."""
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "DELETE 42"
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=mock_cm)

        result = await purge_position_snapshots(mock_pool)  # type: ignore[arg-type]

        assert result == 42

    async def test_handles_large_delete_count(self) -> None:
        """Large row counts must be parsed correctly (no int overflow)."""
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "DELETE 1000000"
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=mock_cm)

        result = await purge_position_snapshots(mock_pool)  # type: ignore[arg-type]

        assert result == 1000000

    async def test_calls_delete_exactly_once(self) -> None:
        """Must issue exactly one DELETE — no extra queries."""
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "DELETE 5"
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=mock_cm)

        await purge_position_snapshots(mock_pool)  # type: ignore[arg-type]

        assert mock_conn.execute.call_count == 1
        mock_conn.execute.assert_called_once_with("DELETE FROM pos_snapshot")


# ---------------------------------------------------------------------------
# Invariant guard — SQL injection attempt on table name
# ---------------------------------------------------------------------------


class TestPurgePositionSnapshotsInvariantGuard:
    """The function must not accept user input for table name (no SQL injection vector)."""

    async def test_table_name_is_hardcoded_no_input_parameter(self) -> None:
        """pos_snapshot table name must be hardcoded — no parameterization gap."""
        source = inspect.getsource(purge_position_snapshots)
        # The function signature should only accept `pool`, no table_name parameter.
        sig = inspect.signature(purge_position_snapshots)
        param_names = list(sig.parameters.keys())
        assert param_names == ["pool"], (
            f"purge_position_snapshots must not accept a table_name parameter; got {param_names}"
        )
        # The DELETE statement must not contain any f-string or format call.
        assert 'f"' not in source and "f'" not in source, (
            "DELETE statement must not use f-string interpolation"
        )
        assert ".format(" not in source, "DELETE statement must not use .format()"
        assert "%s" not in source, "DELETE statement must not use % formatting"
