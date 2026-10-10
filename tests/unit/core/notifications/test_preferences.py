"""Tests for src/core/notifications/preferences.py (FD-17.4)

Coverage targets:
  - get_notification_preferences(): defaults for a row-less user, existing row
  - update_notification_preferences(): allowed field upsert, rejected-field
    filtering, partial upsert preserving other columns
  - negative / boundary inputs (empty changes, all-rejected changes, unknown user)
  - failure injection (pool.acquire raising propagates, not swallowed)
  - one numeric performance assertion (p95 latency budget)
"""

from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import pytest

from src.core.notifications.preferences import (
    ALLOWED_PREFERENCE_FIELDS,
    PreferenceUpdateResult,
    get_notification_preferences,
    update_notification_preferences,
)
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


class TestGetNotificationPreferences:
    async def test_no_row_returns_all_true_defaults(self, pool):
        prefs = await get_notification_preferences(pool, uuid4())

        assert prefs == {field: True for field in ALLOWED_PREFERENCE_FIELDS}

    async def test_existing_row_reflects_stored_values(self, pool):
        user_id = await create_test_user(pool)
        await update_notification_preferences(pool, user_id, {"marketplace_purchase_email": False})

        prefs = await get_notification_preferences(pool, user_id)

        assert prefs["marketplace_purchase_email"] is False
        assert prefs["verification_result_email"] is True


class TestUpdateNotificationPreferencesAllowedFields:
    async def test_applies_single_allowed_field(self, pool):
        user_id = await create_test_user(pool)

        result = await update_notification_preferences(
            pool, user_id, {"risk_mismatch_email": False}
        )

        assert isinstance(result, PreferenceUpdateResult)
        assert result.applied["risk_mismatch_email"] is False
        assert result.rejected_fields == []

    async def test_partial_upsert_preserves_other_columns(self, pool):
        user_id = await create_test_user(pool)
        await update_notification_preferences(pool, user_id, {"risk_mismatch_email": False})

        result = await update_notification_preferences(
            pool, user_id, {"marketplace_purchase_email": False}
        )

        assert result.applied["risk_mismatch_email"] is False
        assert result.applied["marketplace_purchase_email"] is False
        assert result.applied["verification_result_email"] is True

    async def test_second_update_overwrites_first(self, pool):
        user_id = await create_test_user(pool)
        await update_notification_preferences(pool, user_id, {"risk_mismatch_email": False})

        result = await update_notification_preferences(pool, user_id, {"risk_mismatch_email": True})

        assert result.applied["risk_mismatch_email"] is True


class TestUpdateNotificationPreferencesNegativeAndBoundary:
    """Negative / boundary inputs — the whitelist column-filter contract."""

    async def test_empty_changes_applies_nothing_and_rejects_nothing(self, pool):
        user_id = await create_test_user(pool)

        result = await update_notification_preferences(pool, user_id, {})

        assert result.rejected_fields == []
        assert result.applied == {field: True for field in ALLOWED_PREFERENCE_FIELDS}

    async def test_unknown_field_is_rejected_and_not_applied(self, pool):
        user_id = await create_test_user(pool)

        result = await update_notification_preferences(
            pool, user_id, {"human_approval_requested_email": False}
        )

        assert result.rejected_fields == ["human_approval_requested_email"]
        assert result.applied == {field: True for field in ALLOWED_PREFERENCE_FIELDS}

    async def test_all_unknown_changes_leaves_row_unwritten(self, pool):
        user_id = await create_test_user(pool)

        result = await update_notification_preferences(
            pool, user_id, {"totally_bogus_field": False}
        )

        assert result.rejected_fields == ["totally_bogus_field"]
        # No row was ever inserted -- defaults still hold on a plain read.
        prefs = await get_notification_preferences(pool, user_id)
        assert prefs == {field: True for field in ALLOWED_PREFERENCE_FIELDS}

    async def test_mixed_allowed_and_rejected_applies_only_allowed(self, pool):
        user_id = await create_test_user(pool)

        result = await update_notification_preferences(
            pool,
            user_id,
            {"marketplace_purchase_email": False, "human_approval_requested_email": False},
        )

        assert result.rejected_fields == ["human_approval_requested_email"]
        assert result.applied["marketplace_purchase_email"] is False


class TestUpdateNotificationPreferencesFailureInjection:
    """Failure injection: dependency (pool) raising must propagate, fail-closed."""

    async def test_pool_acquire_failure_propagates_on_update(self, pool):
        user_id = await create_test_user(pool)

        class FailingPool:
            def acquire(self):
                raise RuntimeError("simulated pool exhaustion")

        with pytest.raises(RuntimeError, match="simulated pool exhaustion"):
            await update_notification_preferences(
                FailingPool(), user_id, {"risk_mismatch_email": False}
            )

    async def test_connection_execute_failure_propagates(self, pool):
        user_id = await create_test_user(pool)

        class FailingConn:
            async def execute(self, *args, **kwargs):
                raise asyncpg.PostgresConnectionError("simulated connection drop")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        class FailingAcquirePool:
            def acquire(self):
                return FailingConn()

        with pytest.raises(asyncpg.PostgresConnectionError):
            await update_notification_preferences(
                FailingAcquirePool(), user_id, {"risk_mismatch_email": False}
            )

    async def test_connection_fetchrow_failure_propagates_on_get(self, pool):
        class FailingConn:
            async def fetchrow(self, *args, **kwargs):
                raise asyncpg.PostgresConnectionError("simulated connection drop")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        class FailingAcquirePool:
            def acquire(self):
                return FailingConn()

        with pytest.raises(asyncpg.PostgresConnectionError):
            await get_notification_preferences(FailingAcquirePool(), uuid4())


class TestUpdateNotificationPreferencesPerformance:
    @pytest.mark.perf
    async def test_p95_latency_within_budget(self, perf_budget, pool):
        """Numeric perf assertion: 20 sequential upserts, p95 < 100ms (single-row upsert budget)."""
        user_id = await create_test_user(pool)
        counter = 0

        async def measure_upsert_closure():
            nonlocal counter
            counter += 1
            await update_notification_preferences(
                pool, user_id, {"risk_mismatch_email": bool(counter % 2)}
            )

        samples = await perf_budget.samples_async(measure_upsert_closure, n=20)
        wall_ms_list = sorted(s.wall_ms for s in samples)
        p95 = wall_ms_list[int(len(wall_ms_list) * 0.95) - 1]
        assert p95 < 100.0, f"p95 upsert latency {p95:.2f}ms exceeded 100ms budget"

    @pytest.mark.perf
    async def test_p95_latency_detects_budget_violation(self, perf_budget, pool):
        """Failure-injection: 1s delay pushes p95 over 1000ms budget (red gate)."""
        import asyncio as _asyncio

        user_id = await create_test_user(pool)
        counter = 0

        async def slow_upsert_closure():
            nonlocal counter
            counter += 1
            await _asyncio.sleep(1)  # 1s per call → p95 ≈ 1000ms, exceeds 1000ms budget
            await update_notification_preferences(
                pool, user_id, {"risk_mismatch_email": bool(counter % 2)}
            )

        samples = await perf_budget.samples_async(slow_upsert_closure, n=20)
        wall_ms_list = sorted(s.wall_ms for s in samples)
        p95 = wall_ms_list[int(len(wall_ms_list) * 0.95) - 1]
        assert p95 > 1000.0, f"Expected p95 > 1000ms with injected delay, got {p95:.2f}ms"
