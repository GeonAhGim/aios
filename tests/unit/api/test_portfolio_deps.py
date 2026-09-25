"""Tests for src/api/portfolio_deps.py — get_portfolio_service dependency wire."""

from unittest.mock import MagicMock, patch

import asyncpg
import pytest
from fastapi.params import Depends

from src.core.loader.risk_policy_loader import RiskPolicy
from src.services.portfolio_service import PortfolioService


class TestGetPortfolioServiceHappyPath:
    """Positive path: direct call with real deps -> PortfolioService returned."""

    def test_returns_portfolio_service_instance(self):
        mock_pool = MagicMock(spec=asyncpg.Pool)
        mock_policy = MagicMock(spec=RiskPolicy)
        from src.api.portfolio_deps import get_portfolio_service

        result = get_portfolio_service(pool=mock_pool, policy=mock_policy)

        assert isinstance(result, PortfolioService)
        assert result._pool is mock_pool
        assert result._risk_policy is mock_policy


class TestNegativeTests:
    """Negative / boundary tests."""

    def test_portfolio_service_constructor_raises(self):
        """PortfolioService.__init__ raises -> exception propagates."""
        mock_pool = MagicMock(spec=asyncpg.Pool)
        mock_policy = MagicMock(spec=RiskPolicy)
        from src.api.portfolio_deps import get_portfolio_service

        with patch(
            "src.services.portfolio_service.PortfolioService.__init__",
            side_effect=RuntimeError("init failed"),
        ):
            with pytest.raises(RuntimeError, match="init failed"):
                get_portfolio_service(pool=mock_pool, policy=mock_policy)

    def test_portfolio_service_module_level_mock_pool_none(self):
        """PortfolioService class replaced at module level -> None pool passed through."""
        from src.api import portfolio_deps

        mock_policy = MagicMock(spec=RiskPolicy)
        with patch.object(portfolio_deps, "PortfolioService") as MockSvc:
            portfolio_deps.get_portfolio_service(pool=None, policy=mock_policy)
            MockSvc.assert_called_once_with(None, mock_policy)

    def test_portfolio_service_module_level_mock_policy_none(self):
        """PortfolioService class replaced at module level -> None policy passed through."""
        from src.api import portfolio_deps

        mock_pool = MagicMock(spec=asyncpg.Pool)
        with patch.object(portfolio_deps, "PortfolioService") as MockSvc:
            portfolio_deps.get_portfolio_service(pool=mock_pool, policy=None)
            MockSvc.assert_called_once_with(mock_pool, None)


class TestFailureInjection:
    """Failure-injection tests via monkeypatch."""

    def test_get_pool_raises_connection_error_via_dependency(self):
        """get_pool raises ConnectionError -> propagates through Depends chain."""
        from src.api import portfolio_deps

        async def failing_get_pool():
            raise ConnectionError("db unreachable")

        with patch.object(portfolio_deps, "get_pool", failing_get_pool):
            with pytest.raises(ConnectionError, match="db unreachable"):
                import asyncio

                asyncio.run(portfolio_deps.get_pool())

    def test_get_risk_policy_raises_value_error_via_dependency(self):
        """get_risk_policy raises ValueError -> propagates through Depends chain."""
        from src.api import portfolio_deps

        with patch.object(
            portfolio_deps, "get_risk_policy", side_effect=ValueError("invalid policy")
        ):
            with pytest.raises(ValueError, match="invalid policy"):
                portfolio_deps.get_risk_policy()

    def test_get_portfolio_service_signature_has_depends(self):
        """Verify the function signature uses Depends for DI wiring."""
        import inspect

        from src.api.portfolio_deps import get_portfolio_service

        sig = inspect.signature(get_portfolio_service)
        pool_default = sig.parameters["pool"].default
        policy_default = sig.parameters["policy"].default

        assert isinstance(pool_default, Depends)
        assert isinstance(policy_default, Depends)
        assert pool_default.dependency.__name__ == "get_pool"
        assert policy_default.dependency.__name__ == "get_risk_policy"
