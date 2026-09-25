"""2.12b — Exception hierarchy tests.

Covers every class in src/core/exceptions.py:
  MihwaError, CurrencyMismatchError, ExchangeAPIError,
  RetryableExchangeError, FatalExchangeError,
  ZoneViolationError, FrozenZoneLiveModeBlockedError,
  FrozenZonePaperAdapterBlockedError, EventHandlerError.
"""

from __future__ import annotations

import pytest


class TestMihwaError:
    """MihwaError is the root of the custom exception tree."""

    def test_is_exception(self):
        from src.core.exceptions import MihwaError

        assert issubclass(MihwaError, Exception)

    def test_instantiate_without_args(self):
        from src.core.exceptions import MihwaError

        exc = MihwaError()
        assert str(exc) == ""

    def test_instantiate_with_message(self):
        from src.core.exceptions import MihwaError

        exc = MihwaError("something went wrong")
        assert str(exc) == "something went wrong"

    def test_repr_contains_class_name(self):
        from src.core.exceptions import MihwaError

        exc = MihwaError("boom")
        assert "MihwaError" in repr(exc)

    def test_catch_as_exception(self):
        from src.core.exceptions import MihwaError

        with pytest.raises(MihwaError):
            raise MihwaError("caught")

    def test_isinstance_of_exception(self):
        from src.core.exceptions import MihwaError

        exc = MihwaError()
        assert isinstance(exc, Exception)
        assert isinstance(exc, BaseException)


class TestCurrencyMismatchError:
    """CurrencyMismatchError carries two currency labels."""

    def test_message_format(self):
        from src.core.exceptions import CurrencyMismatchError

        exc = CurrencyMismatchError("KRW", "USD")
        assert "통화 불일치" in str(exc)
        assert "KRW" in str(exc)
        assert "USD" in str(exc)

    def test_inherits_mihwa_error(self):
        from src.core.exceptions import CurrencyMismatchError, MihwaError

        assert issubclass(CurrencyMismatchError, MihwaError)

    def test_negative_empty_strings(self):
        """Boundary: empty currency strings should still produce a message."""
        from src.core.exceptions import CurrencyMismatchError

        exc = CurrencyMismatchError("", "")
        assert str(exc) == "통화 불일치:  vs "

    def test_negative_none_like(self):
        """Boundary: non-string 'Currency' objects (e.g. None) should still render."""
        from src.core.exceptions import CurrencyMismatchError

        exc = CurrencyMismatchError(str(None), "USD")
        assert "None" in str(exc)

    def test_repr_contains_class_name(self):
        from src.core.exceptions import CurrencyMismatchError

        exc = CurrencyMismatchError("KRW", "JPY")
        assert "CurrencyMismatchError" in repr(exc)


class TestExchangeAPIError:
    """ExchangeAPIError is a MihwaError subclass with no custom __init__."""

    def test_inherits_mihwa_error(self):
        from src.core.exceptions import ExchangeAPIError, MihwaError

        assert issubclass(ExchangeAPIError, MihwaError)

    def test_instantiate_with_message(self):
        from src.core.exceptions import ExchangeAPIError

        exc = ExchangeAPIError("rate limit")
        assert str(exc) == "rate limit"

    def test_catch_as_mihwa_error(self):
        """ExchangeAPIError IS a MihwaError — it must be caught by except MihwaError."""
        from src.core.exceptions import ExchangeAPIError, MihwaError

        caught = False
        try:
            raise ExchangeAPIError("network timeout")
        except MihwaError:
            caught = True
        assert caught is True

    def test_repr_contains_class_name(self):
        from src.core.exceptions import ExchangeAPIError

        exc = ExchangeAPIError("bad request")
        assert "ExchangeAPIError" in repr(exc)


class TestRetryableExchangeError:
    """RetryableExchangeError signals a transient failure."""

    def test_inherits_exchange_api_error(self):
        from src.core.exceptions import ExchangeAPIError, RetryableExchangeError

        assert issubclass(RetryableExchangeError, ExchangeAPIError)

    def test_inherits_mihwa_error(self):
        from src.core.exceptions import MihwaError, RetryableExchangeError

        assert issubclass(RetryableExchangeError, MihwaError)

    def test_instantiate_with_message(self):
        from src.core.exceptions import RetryableExchangeError

        exc = RetryableExchangeError("503 Service Unavailable")
        assert str(exc) == "503 Service Unavailable"

    def test_catch_as_exchange_api_error(self):
        from src.core.exceptions import ExchangeAPIError, RetryableExchangeError

        caught = False
        try:
            raise RetryableExchangeError("timeout")
        except ExchangeAPIError:
            caught = True
        assert caught is True

    def test_negative_empty_message(self):
        """Boundary: empty message should still be a valid instance."""
        from src.core.exceptions import RetryableExchangeError

        exc = RetryableExchangeError("")
        assert str(exc) == ""


class TestFatalExchangeError:
    """FatalExchangeError signals a non-retryable failure."""

    def test_inherits_exchange_api_error(self):
        from src.core.exceptions import ExchangeAPIError, FatalExchangeError

        assert issubclass(FatalExchangeError, ExchangeAPIError)

    def test_inherits_mihwa_error(self):
        from src.core.exceptions import FatalExchangeError, MihwaError

        assert issubclass(FatalExchangeError, MihwaError)

    def test_instantiate_with_message(self):
        from src.core.exceptions import FatalExchangeError

        exc = FatalExchangeError("invalid API key")
        assert str(exc) == "invalid API key"

    def test_catch_as_exchange_api_error(self):
        from src.core.exceptions import ExchangeAPIError, FatalExchangeError

        caught = False
        try:
            raise FatalExchangeError("forbidden")
        except ExchangeAPIError:
            caught = True
        assert caught is True

    def test_negative_empty_message(self):
        """Boundary: empty message should still be a valid instance."""
        from src.core.exceptions import FatalExchangeError

        exc = FatalExchangeError("")
        assert str(exc) == ""


class TestZoneViolationError:
    """ZoneViolationError blocks SCAFFOLD code from importing FROZEN zones."""

    def test_inherits_mihwa_error(self):
        from src.core.exceptions import MihwaError, ZoneViolationError

        assert issubclass(ZoneViolationError, MihwaError)

    def test_instantiate_with_message(self):
        from src.core.exceptions import ZoneViolationError

        exc = ZoneViolationError("core imports foundation")
        assert str(exc) == "core imports foundation"

    def test_negative_empty_message(self):
        """Boundary: empty message should still be a valid instance."""
        from src.core.exceptions import ZoneViolationError

        exc = ZoneViolationError("")
        assert str(exc) == ""


class TestFrozenZoneLiveModeBlockedError:
    """ADR-2026-08-29-E — blocks live mode for FROZEN-PAPER-ONLY."""

    def test_inherits_mihwa_error(self):
        from src.core.exceptions import FrozenZoneLiveModeBlockedError, MihwaError

        assert issubclass(FrozenZoneLiveModeBlockedError, MihwaError)

    def test_instantiate_with_message(self):
        from src.core.exceptions import FrozenZoneLiveModeBlockedError

        exc = FrozenZoneLiveModeBlockedError("mode=live")
        assert str(exc) == "mode=live"

    def test_negative_empty_message(self):
        """Boundary: empty message should still be a valid instance."""
        from src.core.exceptions import FrozenZoneLiveModeBlockedError

        exc = FrozenZoneLiveModeBlockedError("")
        assert str(exc) == ""


class TestFrozenZonePaperAdapterBlockedError:
    """Fail-closed guard: live-configured adapter in PAPER execution."""

    def test_inherits_mihwa_error(self):
        from src.core.exceptions import FrozenZonePaperAdapterBlockedError, MihwaError

        assert issubclass(FrozenZonePaperAdapterBlockedError, MihwaError)

    def test_instantiate_with_message(self):
        from src.core.exceptions import FrozenZonePaperAdapterBlockedError

        exc = FrozenZonePaperAdapterBlockedError("nh adapter in paper")
        assert str(exc) == "nh adapter in paper"

    def test_negative_empty_message(self):
        """Boundary: empty message should still be a valid instance."""
        from src.core.exceptions import FrozenZonePaperAdapterBlockedError

        exc = FrozenZonePaperAdapterBlockedError("")
        assert str(exc) == ""


class TestEventHandlerError:
    """EventHandlerError wraps failures in event dispatch."""

    def test_inherits_mihwa_error(self):
        from src.core.exceptions import EventHandlerError, MihwaError

        assert issubclass(EventHandlerError, MihwaError)

    def test_instantiate_with_message(self):
        from src.core.exceptions import EventHandlerError

        exc = EventHandlerError("handler crashed")
        assert str(exc) == "handler crashed"

    def test_negative_empty_message(self):
        """Boundary: empty message should still be a valid instance."""
        from src.core.exceptions import EventHandlerError

        exc = EventHandlerError("")
        assert str(exc) == ""


class TestFailureInjection:
    """Failure-injection tests — monkeypatch/raise to verify exception chains."""

    def test_exchange_api_error_chain(self):
        """Verify ExchangeAPIError propagates through nested try/except correctly."""
        from src.core.exceptions import (
            ExchangeAPIError,
            FatalExchangeError,
            MihwaError,
            RetryableExchangeError,
        )

        # RetryableExchangeError should be caught by ExchangeAPIError handler
        caught_by_api = False
        try:
            try:
                raise RetryableExchangeError("timeout")
            except ExchangeAPIError:
                caught_by_api = True
        except MihwaError:
            pytest.fail("RetryableExchangeError should not escape ExchangeAPIError catch")
        assert caught_by_api is True

        # FatalExchangeError should also be caught by ExchangeAPIError handler
        caught_by_api = False
        try:
            try:
                raise FatalExchangeError("auth failed")
            except ExchangeAPIError:
                caught_by_api = True
        except MihwaError:
            pytest.fail("FatalExchangeError should not escape ExchangeAPIError catch")
        assert caught_by_api is True

    def test_hierarchy_chain_all_the_way_down(self):
        """Every leaf exception must be catchable as MihwaError."""
        from src.core.exceptions import (
            CurrencyMismatchError,
            FatalExchangeError,
            FrozenZoneLiveModeBlockedError,
            FrozenZonePaperAdapterBlockedError,
            MihwaError,
            RetryableExchangeError,
            ZoneViolationError,
        )

        leaves = [
            CurrencyMismatchError("KRW", "USD"),
            RetryableExchangeError("timeout"),
            FatalExchangeError("auth"),
            ZoneViolationError("violation"),
            FrozenZoneLiveModeBlockedError("live"),
            FrozenZonePaperAdapterBlockedError("adapter"),
        ]

        for exc in leaves:
            caught = False
            try:
                raise exc
            except MihwaError:
                caught = True
            assert caught is True, f"{exc.__class__.__name__} should be catchable as MihwaError"

    def test_exception_traceback_preserved(self):
        """Verify that exception __cause__ chain works for logged errors."""
        from src.core.exceptions import MihwaError

        try:
            try:
                raise ValueError("underlying")
            except ValueError as exc:
                raise MihwaError("wrapped") from exc
        except MihwaError as exc:
            assert exc.__cause__ is not None
            assert isinstance(exc.__cause__, ValueError)

    def test_exception_args_preserved(self):
        """Verify that exception args tuple contains the message."""
        from src.core.exceptions import ExchangeAPIError

        exc = ExchangeAPIError("detail")
        assert len(exc.args) == 1
        assert exc.args[0] == "detail"
