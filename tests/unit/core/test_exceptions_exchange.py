"""2.12b — Exchange API error and retry classification contracts."""

from __future__ import annotations


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

    def test_failure_injection_raises_in_handler(self):
        """Failure injection: ExchangeAPIError propagates through nested handler."""
        from src.core.exceptions import ExchangeAPIError

        caught = False
        try:
            raise ExchangeAPIError("connection refused")
        except ExchangeAPIError as exc:
            caught = True
            assert str(exc) == "connection refused"
        assert caught is True


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

    def test_failure_injection_raises_in_handler(self):
        """Failure injection: RetryableExchangeError propagates through nested handler."""
        from src.core.exceptions import RetryableExchangeError

        caught = False
        try:
            raise RetryableExchangeError("503 retry later")
        except RetryableExchangeError as exc:
            caught = True
            assert str(exc) == "503 retry later"
        assert caught is True

    def test_failure_injection_catch_as_mihwa_error(self):
        """RetryableExchangeError caught by parent MihwaError handler."""
        from src.core.exceptions import MihwaError, RetryableExchangeError

        caught = False
        try:
            raise RetryableExchangeError("transient")
        except MihwaError:
            caught = True
        assert caught is True


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

    def test_failure_injection_raises_in_handler(self):
        """Failure injection: FatalExchangeError propagates through nested handler."""
        from src.core.exceptions import FatalExchangeError

        caught = False
        try:
            raise FatalExchangeError("auth expired")
        except FatalExchangeError as exc:
            caught = True
            assert str(exc) == "auth expired"
        assert caught is True

    def test_failure_injection_catch_as_mihwa_error(self):
        """FatalExchangeError caught by parent MihwaError handler."""
        from src.core.exceptions import FatalExchangeError, MihwaError

        caught = False
        try:
            raise FatalExchangeError("permanent")
        except MihwaError:
            caught = True
        assert caught is True
