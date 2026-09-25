"""Tests for src/api/strategy_builder_deps.py — service factory dependency wires."""

import inspect
from unittest.mock import MagicMock, patch

import asyncpg
import pytest
from fastapi.params import Depends

from src.core.indicators.talib_adapter import IndicatorService
from src.services.strategy_builder_service import StrategyBuilderService


class TestHappyPath:
    """Positive path: direct call with real deps -> expected instance returned."""

    def test_get_indicator_service_returns_indicator_service(self):
        from src.api.strategy_builder_deps import get_indicator_service

        result = get_indicator_service()

        assert isinstance(result, IndicatorService)

    def test_get_strategy_builder_service_returns_service_with_pool(self):
        mock_pool = MagicMock(spec=asyncpg.Pool)
        from src.api.strategy_builder_deps import get_strategy_builder_service

        result = get_strategy_builder_service(pool=mock_pool)

        assert isinstance(result, StrategyBuilderService)
        assert result._pool is mock_pool


class TestNegativeTests:
    """Negative / boundary tests."""

    def test_get_strategy_builder_service_pool_none(self):
        """None pool passed through unchanged — no validation at this layer."""
        from src.api.strategy_builder_deps import get_strategy_builder_service

        result = get_strategy_builder_service(pool=None)

        assert isinstance(result, StrategyBuilderService)
        assert result._pool is None

    def test_get_strategy_builder_service_constructor_raises(self):
        """StrategyBuilderService.__init__ raises -> exception propagates unmodified."""
        mock_pool = MagicMock(spec=asyncpg.Pool)
        from src.api.strategy_builder_deps import get_strategy_builder_service

        with patch(
            "src.services.strategy_builder_service.StrategyBuilderService.__init__",
            side_effect=RuntimeError("init failed"),
        ):
            with pytest.raises(RuntimeError, match="init failed"):
                get_strategy_builder_service(pool=mock_pool)

    def test_get_strategy_builder_service_module_level_mock(self):
        """StrategyBuilderService class replaced at module level -> pool forwarded as-is."""
        from src.api import strategy_builder_deps

        mock_pool = MagicMock(spec=asyncpg.Pool)
        with patch.object(strategy_builder_deps, "StrategyBuilderService") as MockSvc:
            strategy_builder_deps.get_strategy_builder_service(pool=mock_pool)
            MockSvc.assert_called_once_with(mock_pool)

    def test_get_indicator_service_ignores_unexpected_args(self):
        """get_indicator_service takes no parameters — calling with one raises TypeError."""
        from src.api.strategy_builder_deps import get_indicator_service

        with pytest.raises(TypeError):
            get_indicator_service(MagicMock())  # type: ignore[call-arg]


class TestFailureInjection:
    """Failure-injection tests via monkeypatch."""

    def test_indicator_service_constructor_raises_propagates(self):
        """IndicatorService() raising at construction time propagates through the factory."""
        from src.api import strategy_builder_deps

        with patch.object(
            strategy_builder_deps,
            "IndicatorService",
            side_effect=RuntimeError("talib unavailable"),
        ):
            with pytest.raises(RuntimeError, match="talib unavailable"):
                strategy_builder_deps.get_indicator_service()

    def test_get_strategy_builder_service_signature_has_depends(self):
        """Verify the function signature wires pool via Depends(get_pool)."""
        from src.api.strategy_builder_deps import get_strategy_builder_service

        sig = inspect.signature(get_strategy_builder_service)
        pool_default = sig.parameters["pool"].default

        assert isinstance(pool_default, Depends)
        assert pool_default.dependency.__name__ == "get_pool"
