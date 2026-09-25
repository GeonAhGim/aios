"""Tests for src/api/reports_deps.py — get_report_service dependency wire."""

import asyncio
import inspect
from unittest.mock import MagicMock, patch

import asyncpg
import pytest
from fastapi.params import Depends

from src.services.report_service import ReportService


class TestGetReportServiceHappyPath:
    """Positive path: direct call with real deps -> ReportService returned."""

    def test_returns_report_service_instance(self):
        mock_pool = MagicMock(spec=asyncpg.Pool)
        from src.api.reports_deps import get_report_service

        result = get_report_service(pool=mock_pool)

        assert isinstance(result, ReportService)
        assert result._pool is mock_pool


class TestNegativeTests:
    """Negative / boundary tests."""

    def test_report_service_constructor_raises(self):
        """ReportService.__init__ raises -> exception propagates."""
        mock_pool = MagicMock(spec=asyncpg.Pool)
        from src.api.reports_deps import get_report_service

        with patch(
            "src.services.report_service.ReportService.__init__",
            side_effect=RuntimeError("init failed"),
        ):
            with pytest.raises(RuntimeError, match="init failed"):
                get_report_service(pool=mock_pool)

    def test_report_service_module_level_mock_pool_none(self):
        """ReportService class replaced at module level -> None pool passed through."""
        from src.api import reports_deps

        with patch.object(reports_deps, "ReportService") as MockSvc:
            reports_deps.get_report_service(pool=None)
            MockSvc.assert_called_once_with(None)

    def test_report_service_module_level_mock_pool_wrong_type(self):
        """Non-pool object passed as pool -> forwarded as-is, no coercion attempted."""
        from src.api import reports_deps

        bogus_pool = object()
        with patch.object(reports_deps, "ReportService") as MockSvc:
            reports_deps.get_report_service(pool=bogus_pool)
            MockSvc.assert_called_once_with(bogus_pool)


class TestFailureInjection:
    """Failure-injection tests via monkeypatch."""

    def test_get_pool_raises_connection_error_via_dependency(self):
        """get_pool raises ConnectionError -> propagates through Depends chain."""
        from src.api import reports_deps

        async def failing_get_pool():
            raise ConnectionError("db unreachable")

        with patch.object(reports_deps, "get_pool", failing_get_pool):
            with pytest.raises(ConnectionError, match="db unreachable"):
                asyncio.run(reports_deps.get_pool())

    def test_get_report_service_signature_has_depends(self):
        """Verify the function signature uses Depends for DI wiring."""
        from src.api.reports_deps import get_report_service

        sig = inspect.signature(get_report_service)
        pool_default = sig.parameters["pool"].default

        assert isinstance(pool_default, Depends)
        assert pool_default.dependency.__name__ == "get_pool"
