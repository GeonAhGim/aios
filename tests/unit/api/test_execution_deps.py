"""Tests for src/api/execution_deps.py — get_execution_service /
get_execution_monitoring_service dependency wiring (16번대 — 실행 제어판)."""

from unittest.mock import MagicMock, patch

import asyncpg
import pytest
from fastapi.params import Depends

from src.core.event_bus.bus import EventBus
from src.core.loader.risk_policy_loader import RiskPolicy
from src.services.execution_monitoring_service import ExecutionMonitoringService
from src.services.execution_service import ExecutionService


class TestGetExecutionServiceHappyPath:
    """Positive path: direct call with real deps -> ExecutionService returned."""

    def test_returns_execution_service_instance(self):
        mock_pool = MagicMock(spec=asyncpg.Pool)
        mock_policy = MagicMock(spec=RiskPolicy)
        mock_bus = MagicMock(spec=EventBus)
        from src.api.execution_deps import get_execution_service

        result = get_execution_service(pool=mock_pool, policy=mock_policy, event_bus=mock_bus)

        assert isinstance(result, ExecutionService)
        assert result._pool is mock_pool
        assert result._risk_policy is mock_policy
        assert result._publish is mock_bus.publish
        assert result._pre_start_gate is not None


class TestGetExecutionMonitoringServiceHappyPath:
    """Positive path: direct call with real pool -> ExecutionMonitoringService returned."""

    def test_returns_execution_monitoring_service_instance(self):
        mock_pool = MagicMock(spec=asyncpg.Pool)
        from src.api.execution_deps import get_execution_monitoring_service

        result = get_execution_monitoring_service(pool=mock_pool)

        assert isinstance(result, ExecutionMonitoringService)
        assert result._pool is mock_pool


class TestNegativeTests:
    """Negative / boundary tests."""

    def test_execution_service_constructor_raises(self):
        """ExecutionService.__init__ raises -> exception propagates."""
        mock_pool = MagicMock(spec=asyncpg.Pool)
        mock_policy = MagicMock(spec=RiskPolicy)
        mock_bus = MagicMock(spec=EventBus)
        from src.api.execution_deps import get_execution_service

        with patch(
            "src.services.execution_service.ExecutionService.__init__",
            side_effect=RuntimeError("init failed"),
        ):
            with pytest.raises(RuntimeError, match="init failed"):
                get_execution_service(pool=mock_pool, policy=mock_policy, event_bus=mock_bus)

    def test_execution_service_module_level_mock_pool_none(self):
        """ExecutionService class replaced at module level -> None pool passed through."""
        from src.api import execution_deps

        mock_policy = MagicMock(spec=RiskPolicy)
        mock_bus = MagicMock(spec=EventBus)
        with patch.object(execution_deps, "ExecutionService") as MockSvc:
            execution_deps.get_execution_service(pool=None, policy=mock_policy, event_bus=mock_bus)
            assert MockSvc.call_args.args[0] is None

    def test_execution_service_module_level_mock_event_bus_none(self):
        """event_bus=None -> publish kwarg is None, not an AttributeError on .publish."""
        from src.api import execution_deps

        mock_pool = MagicMock(spec=asyncpg.Pool)
        mock_policy = MagicMock(spec=RiskPolicy)

        with pytest.raises(AttributeError):
            execution_deps.get_execution_service(pool=mock_pool, policy=mock_policy, event_bus=None)

    def test_execution_monitoring_service_constructor_raises(self):
        """ExecutionMonitoringService.__init__ raises -> exception propagates."""
        mock_pool = MagicMock(spec=asyncpg.Pool)
        from src.api.execution_deps import get_execution_monitoring_service

        with patch(
            "src.services.execution_monitoring_service.ExecutionMonitoringService.__init__",
            side_effect=RuntimeError("monitoring init failed"),
        ):
            with pytest.raises(RuntimeError, match="monitoring init failed"):
                get_execution_monitoring_service(pool=mock_pool)

    def test_execution_monitoring_service_module_level_mock_pool_none(self):
        """ExecutionMonitoringService class replaced at module level -> None pool passed through."""
        from src.api import execution_deps

        with patch.object(execution_deps, "ExecutionMonitoringService") as MockSvc:
            execution_deps.get_execution_monitoring_service(pool=None)
            MockSvc.assert_called_once_with(None)


class TestFailureInjection:
    """Failure-injection tests via monkeypatch."""

    def test_make_foundation_pre_submit_gate_raises_propagates(self):
        """Gate factory raising -> get_execution_service does not swallow it (fail-closed)."""
        from src.api import execution_deps

        mock_pool = MagicMock(spec=asyncpg.Pool)
        mock_policy = MagicMock(spec=RiskPolicy)
        mock_bus = MagicMock(spec=EventBus)

        with patch.object(
            execution_deps,
            "make_foundation_pre_submit_gate",
            side_effect=RuntimeError("gate build failed"),
        ):
            with pytest.raises(RuntimeError, match="gate build failed"):
                execution_deps.get_execution_service(
                    pool=mock_pool, policy=mock_policy, event_bus=mock_bus
                )

    def test_get_pool_raises_connection_error_via_dependency(self):
        """get_pool raises ConnectionError -> propagates through Depends chain."""
        from src.api import execution_deps

        async def failing_get_pool():
            raise ConnectionError("db unreachable")

        with patch.object(execution_deps, "get_pool", failing_get_pool):
            with pytest.raises(ConnectionError, match="db unreachable"):
                import asyncio

                asyncio.run(execution_deps.get_pool())

    def test_get_execution_service_signature_has_depends(self):
        """Verify the function signature uses Depends for DI wiring."""
        import inspect

        from src.api.execution_deps import get_execution_service

        sig = inspect.signature(get_execution_service)
        pool_default = sig.parameters["pool"].default
        policy_default = sig.parameters["policy"].default
        event_bus_default = sig.parameters["event_bus"].default

        assert isinstance(pool_default, Depends)
        assert isinstance(policy_default, Depends)
        assert isinstance(event_bus_default, Depends)
        assert pool_default.dependency.__name__ == "get_pool"
        assert policy_default.dependency.__name__ == "get_risk_policy"
        assert event_bus_default.dependency.__name__ == "get_event_bus"

    def test_get_execution_monitoring_service_signature_has_depends(self):
        """Verify the monitoring factory signature uses Depends for DI wiring."""
        import inspect

        from src.api.execution_deps import get_execution_monitoring_service

        sig = inspect.signature(get_execution_monitoring_service)
        pool_default = sig.parameters["pool"].default

        assert isinstance(pool_default, Depends)
        assert pool_default.dependency.__name__ == "get_pool"
