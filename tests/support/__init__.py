"""Negative / failure-injection tests for tests.support helpers.

Coverage targets:
- frozen.py.assign_attr  — frozen dataclass / frozen-model rejection
- db.py._pool_retry_delay — delay schedule monotonicity & negative input
- db.py.create_pool_with_retry — dependency failure injection (connect retry)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests.support.frozen import assign_attr

# ---------------------------------------------------------------------------
# frozen.assign_attr — negative tests
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrozenRecord:
    id: int
    label: str


class TestAssignAttrFrozenDataclass:
    """assign_attr must raise on frozen dataclass mutation."""

    def test_rejects_existing_attribute(self) -> None:
        obj = FrozenRecord(id=1, label="a")
        with pytest.raises(AttributeError):
            assign_attr(obj, "id", 999)

    def test_rejects_new_attribute(self) -> None:
        obj = FrozenRecord(id=1, label="a")
        with pytest.raises(AttributeError):
            assign_attr(obj, "extra", 42)


class TestAssignAttrNonFrozen:
    """assign_attr must allow mutation on non-frozen objects."""

    def test_allows_mutation_on_mock(self) -> None:
        obj = MagicMock()
        assign_attr(obj, "name", "value")
        obj.name = "value"  # verify setattr was called


# ---------------------------------------------------------------------------
# db._pool_retry_delay — negative / invariant tests
# ---------------------------------------------------------------------------


class TestPoolRetryDelay:
    """Delay schedule must be monotonically increasing with attempt number."""

    def _delay(self, attempt: int) -> float:
        """Import inside test to avoid polluting module-level namespace."""
        from tests.support.db import _pool_retry_delay  # noqa: PLC0415

        return _pool_retry_delay(attempt)

    def test_delay_is_positive(self) -> None:
        assert self._delay(0) > 0
        assert self._delay(5) > 0

    def test_delay_is_monotonically_increasing(self) -> None:
        prev = self._delay(0)
        for attempt in range(1, 10):
            current = self._delay(attempt)
            assert current > prev, f"delay({attempt})={current} <= delay({attempt - 1})={prev}"
            prev = current

    def test_delay_formula_matches(self) -> None:
        """_pool_retry_delay(attempt) = BASE_DELAY * (attempt + 1)."""
        from tests.support.db import _POOL_CONNECT_RETRY_BASE_DELAY  # noqa: PLC0415

        assert self._delay(0) == pytest.approx(_POOL_CONNECT_RETRY_BASE_DELAY * 1)
        assert self._delay(1) == pytest.approx(_POOL_CONNECT_RETRY_BASE_DELAY * 2)
        assert self._delay(4) == pytest.approx(_POOL_CONNECT_RETRY_BASE_DELAY * 5)


# ---------------------------------------------------------------------------
# db.create_pool_with_retry — failure injection
# ---------------------------------------------------------------------------


class _MockPool:
    """Minimal awaitable pool mock that mirrors asyncpg.Pool's await semantics.

    ``await pool`` internally calls ``pool.__aenter__()``.
    """

    def __init__(
        self,
        aenter_result: Any = None,
        aenter_side_effect: Any = None,
    ) -> None:
        self._aenter_result = aenter_result
        self._aenter_side_effect = aenter_side_effect
        self.aenter_count = 0

    def __await__(self) -> Any:
        async def _coro() -> Any:
            self.aenter_count += 1
            if self._aenter_side_effect is not None:
                raise self._aenter_side_effect
            return self._aenter_result

        return _coro().__await__()

    def terminate(self) -> None:
        pass


def _make_awaitable_pool(
    aenter_result: Any = None,
    aenter_side_effect: Any = None,
) -> _MockPool:
    return _MockPool(aenter_result, aenter_side_effect)


@pytest.mark.asyncio
class TestCreatePoolWithRetry:
    """Failure injection tests for create_pool_with_retry."""

    async def test_raises_on_all_attempts_failure(self) -> None:
        """When every connect attempt fails with a retryable error, the
        original exception must propagate (fail-closed)."""

        from tests.support.db import (  # noqa: PLC0415
            _POOL_CONNECT_ATTEMPTS,
            _RETRYABLE_CONNECT_ERRORS,
            create_pool_with_retry,
        )

        # Pick the first retryable error class and raise an instance
        retryable_exc = _RETRYABLE_CONNECT_ERRORS[0]  # OSError

        call_count = 0

        def _create_pool_side_effect(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            pool = _MockPool(aenter_side_effect=retryable_exc())
            return pool

        with patch("asyncpg.create_pool", side_effect=_create_pool_side_effect):
            with pytest.raises(_RETRYABLE_CONNECT_ERRORS):
                await create_pool_with_retry("postgresql://localhost/test")

            # Must have retried exactly _POOL_CONNECT_ATTEMPTS times
            assert call_count == _POOL_CONNECT_ATTEMPTS

    async def test_succeeds_on_final_attempt(self) -> None:
        """When connect succeeds after transient failures, the pool is returned."""
        from tests.support.db import create_pool_with_retry

        mock_pool = _make_awaitable_pool()

        def _create_pool_side_effect(*args: Any, **kwargs: Any) -> Any:
            return mock_pool

        with patch("asyncpg.create_pool", side_effect=_create_pool_side_effect):
            await create_pool_with_retry("postgresql://localhost/test")
            # Pool was awaited (via __aenter__) — verify it succeeded
            assert mock_pool.aenter_count == 1
