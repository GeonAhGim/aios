"""2.12b — Exception hierarchy tests.

Covers base, currency, zone and event errors plus hierarchy-wide contracts:
  MihwaError, CurrencyMismatchError,
  ZoneViolationError, FrozenZoneLiveModeBlockedError,
  FrozenZonePaperAdapterBlockedError, EventHandlerError.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "name",
    [
        "MihwaError",
        "ZoneViolationError",
        "FrozenZoneLiveModeBlockedError",
        "FrozenZonePaperAdapterBlockedError",
        "EventHandlerError",
        "ExchangeAPIError",
        "RetryableExchangeError",
        "FatalExchangeError",
    ],
)
def test_negative_empty_message(name):
    """All message-only errors preserve an explicitly empty argument."""
    from src.core import exceptions

    exc = getattr(exceptions, name)("")
    assert str(exc) == ""
    assert exc.args == ("",)
    assert isinstance(exc, exceptions.MihwaError)


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

    def test_failure_injection_raises_in_handler(self):
        """Failure injection: MihwaError raised inside a nested handler propagates."""
        from src.core.exceptions import MihwaError

        def inner():
            raise MihwaError("injected failure")

        caught = False
        try:
            inner()
        except MihwaError as exc:
            caught = True
            assert str(exc) == "injected failure"
        assert caught is True

    def test_failure_injection_catch_as_base_exception(self):
        """MihwaError is caught by except Exception — verifies inheritance chain."""
        from src.core.exceptions import MihwaError

        caught = False
        try:
            raise MihwaError("base catch test")
        except Exception:  # noqa: BLE001 — intentional: testing Exception-level catch
            caught = True
        assert caught is True


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

    @pytest.mark.parametrize(
        "left,right,expected",
        [
            (None, "USD", "통화 불일치: None vs USD"),
            ("KRW", None, "통화 불일치: KRW vs None"),
            (None, None, "통화 불일치: None vs None"),
        ],
    )
    def test_negative_none_value(self, left, right, expected):
        """Pass actual None values through both constructor boundaries."""
        from src.core.exceptions import CurrencyMismatchError

        exc = CurrencyMismatchError(left, right)
        assert str(exc) == expected
        assert exc.args == (expected,)

    def test_dependency_initializer_failure(self, monkeypatch):
        """A failing parent initializer must propagate without masking its error."""
        from src.core.exceptions import CurrencyMismatchError, MihwaError

        failure = RuntimeError("injected initializer failure")
        calls = []

        def broken_init(self, message):
            calls.append((type(self), message))
            raise failure

        with monkeypatch.context() as patch:
            patch.setattr(MihwaError, "__init__", broken_init)
            with pytest.raises(RuntimeError) as caught:
                CurrencyMismatchError(None, "KRW")

        assert caught.value is failure
        assert calls == [(CurrencyMismatchError, "통화 불일치: None vs KRW")]
        assert CurrencyMismatchError(None, "KRW").args == ("통화 불일치: None vs KRW",)

    def test_repr_contains_class_name(self):
        from src.core.exceptions import CurrencyMismatchError

        exc = CurrencyMismatchError("KRW", "JPY")
        assert "CurrencyMismatchError" in repr(exc)

    def test_failure_injection_raises_in_handler(self):
        """Failure injection: CurrencyMismatchError propagates through nested handler."""
        from src.core.exceptions import CurrencyMismatchError

        caught = False
        try:
            raise CurrencyMismatchError("BTC", "ETH")
        except CurrencyMismatchError as exc:
            caught = True
            assert "통화 불일치" in str(exc)
            assert "BTC" in str(exc)
            assert "ETH" in str(exc)
        assert caught is True

    def test_failure_injection_catch_as_mihwa_error(self):
        """CurrencyMismatchError caught by parent MihwaError handler."""
        from src.core.exceptions import CurrencyMismatchError, MihwaError

        caught = False
        try:
            raise CurrencyMismatchError("USD", "JPY")
        except MihwaError:
            caught = True
        assert caught is True


class TestZoneViolationError:
    """ZoneViolationError blocks SCAFFOLD code from importing FROZEN zones."""

    def test_inherits_mihwa_error(self):
        from src.core.exceptions import MihwaError, ZoneViolationError

        assert issubclass(ZoneViolationError, MihwaError)

    def test_instantiate_with_message(self):
        from src.core.exceptions import ZoneViolationError

        exc = ZoneViolationError("core imports foundation")
        assert str(exc) == "core imports foundation"

    def test_failure_injection_raises_in_handler(self):
        """Failure injection: ZoneViolationError propagates through nested handler."""
        from src.core.exceptions import ZoneViolationError

        caught = False
        try:
            raise ZoneViolationError("frozen import violation")
        except ZoneViolationError as exc:
            caught = True
            assert str(exc) == "frozen import violation"
        assert caught is True

    def test_failure_injection_catch_as_mihwa_error(self):
        """ZoneViolationError caught by parent MihwaError handler."""
        from src.core.exceptions import MihwaError, ZoneViolationError

        caught = False
        try:
            raise ZoneViolationError("violation")
        except MihwaError:
            caught = True
        assert caught is True


class TestFrozenZoneLiveModeBlockedError:
    """ADR-2026-08-29-E — blocks live mode for FROZEN-PAPER-ONLY."""

    def test_inherits_mihwa_error(self):
        from src.core.exceptions import FrozenZoneLiveModeBlockedError, MihwaError

        assert issubclass(FrozenZoneLiveModeBlockedError, MihwaError)

    def test_instantiate_with_message(self):
        from src.core.exceptions import FrozenZoneLiveModeBlockedError

        exc = FrozenZoneLiveModeBlockedError("mode=live")
        assert str(exc) == "mode=live"

    def test_failure_injection_raises_in_handler(self):
        """Failure injection: FrozenZoneLiveModeBlockedError propagates through handler."""
        from src.core.exceptions import FrozenZoneLiveModeBlockedError

        caught = False
        try:
            raise FrozenZoneLiveModeBlockedError("live mode attempt")
        except FrozenZoneLiveModeBlockedError as exc:
            caught = True
            assert str(exc) == "live mode attempt"
        assert caught is True

    def test_failure_injection_catch_as_mihwa_error(self):
        """FrozenZoneLiveModeBlockedError caught by parent MihwaError handler."""
        from src.core.exceptions import FrozenZoneLiveModeBlockedError, MihwaError

        caught = False
        try:
            raise FrozenZoneLiveModeBlockedError("blocked")
        except MihwaError:
            caught = True
        assert caught is True


class TestFrozenZonePaperAdapterBlockedError:
    """Fail-closed guard: live-configured adapter in PAPER execution."""

    def test_inherits_mihwa_error(self):
        from src.core.exceptions import FrozenZonePaperAdapterBlockedError, MihwaError

        assert issubclass(FrozenZonePaperAdapterBlockedError, MihwaError)

    def test_instantiate_with_message(self):
        from src.core.exceptions import FrozenZonePaperAdapterBlockedError

        exc = FrozenZonePaperAdapterBlockedError("nh adapter in paper")
        assert str(exc) == "nh adapter in paper"

    def test_failure_injection_raises_in_handler(self):
        """Failure injection: FrozenZonePaperAdapterBlockedError propagates through handler."""
        from src.core.exceptions import FrozenZonePaperAdapterBlockedError

        caught = False
        try:
            raise FrozenZonePaperAdapterBlockedError("live adapter in paper")
        except FrozenZonePaperAdapterBlockedError as exc:
            caught = True
            assert str(exc) == "live adapter in paper"
        assert caught is True

    def test_failure_injection_catch_as_mihwa_error(self):
        """FrozenZonePaperAdapterBlockedError caught by parent MihwaError handler."""
        from src.core.exceptions import FrozenZonePaperAdapterBlockedError, MihwaError

        caught = False
        try:
            raise FrozenZonePaperAdapterBlockedError("blocked")
        except MihwaError:
            caught = True
        assert caught is True


class TestEventHandlerError:
    """EventHandlerError is a simple MihwaError subclass with no custom __init__."""

    def test_inherits_mihwa_error(self):
        from src.core.exceptions import EventHandlerError, MihwaError

        assert issubclass(EventHandlerError, MihwaError)

    def test_instantiate_with_message(self):
        from src.core.exceptions import EventHandlerError

        exc = EventHandlerError("handler failed")
        assert str(exc) == "handler failed"

    def test_catch_as_mihwa_error(self):
        """EventHandlerError IS a MihwaError — caught by except MihwaError."""
        from src.core.exceptions import EventHandlerError, MihwaError

        caught = False
        try:
            raise EventHandlerError("dispatch error")
        except MihwaError:
            caught = True
        assert caught is True

    def test_failure_injection_raises_in_handler(self):
        """Failure injection: EventHandlerError propagates through nested handler."""
        from src.core.exceptions import EventHandlerError

        caught = False
        try:
            raise EventHandlerError("event processing failed")
        except EventHandlerError as exc:
            caught = True
            assert str(exc) == "event processing failed"
        assert caught is True


class TestAllCustomErrorsInheritMihwaError:
    """Verify that every custom error class inherits from MihwaError.

    Previously this test imported the classes and checked issubclass.
    Now we also verify that instances raised in a handler are actually
    caught by a MihwaError catch block — proving the inheritance chain
    works at runtime, not just at class-definition time.
    """

    def test_all_custom_errors_inherit(self):
        from src.core.exceptions import (
            CurrencyMismatchError,
            EventHandlerError,
            ExchangeAPIError,
            FatalExchangeError,
            FrozenZoneLiveModeBlockedError,
            FrozenZonePaperAdapterBlockedError,
            MihwaError,
            RetryableExchangeError,
            ZoneViolationError,
        )

        all_errors = [
            CurrencyMismatchError,
            EventHandlerError,
            ExchangeAPIError,
            FatalExchangeError,
            FrozenZoneLiveModeBlockedError,
            FrozenZonePaperAdapterBlockedError,
            RetryableExchangeError,
            ZoneViolationError,
        ]

        for error_cls in all_errors:
            assert issubclass(error_cls, MihwaError), (
                f"{error_cls.__name__} must inherit from MihwaError"
            )

    def test_all_errors_caught_by_mihwa_handler(self):
        """Runtime verification: every exception class is caught by except MihwaError."""
        from src.core.exceptions import (
            CurrencyMismatchError,
            EventHandlerError,
            ExchangeAPIError,
            FatalExchangeError,
            FrozenZoneLiveModeBlockedError,
            FrozenZonePaperAdapterBlockedError,
            MihwaError,
            RetryableExchangeError,
            ZoneViolationError,
        )

        all_errors = [
            (CurrencyMismatchError, ("KRW", "USD")),
            (EventHandlerError, ("handler error",)),
            (ExchangeAPIError, ("api error",)),
            (FatalExchangeError, ("fatal",)),
            (FrozenZoneLiveModeBlockedError, ("live",)),
            (FrozenZonePaperAdapterBlockedError, ("adapter",)),
            (RetryableExchangeError, ("retry",)),
            (ZoneViolationError, ("zone",)),
        ]

        for error_cls, args in all_errors:
            caught = False
            try:
                raise error_cls(*args)
            except MihwaError:
                caught = True
            assert caught is True, f"{error_cls.__name__} must be caught by MihwaError"


class TestCurrencyMismatchMessage:
    """CurrencyMismatchError message format verification.

    Previously this test only checked issubclass. Now we verify:
    1. The message contains the Korean prefix and both currency values.
    2. The exception is caught by a MihwaError handler (runtime chain).
    3. The args tuple contains exactly one formatted string.
    """

    def test_currency_mismatch_message(self):
        from src.core.exceptions import CurrencyMismatchError, MihwaError

        exc = CurrencyMismatchError("KRW", "USD")

        # Assertion 1: message format
        msg = str(exc)
        assert "통화 불일치" in msg
        assert "KRW" in msg
        assert "USD" in msg

        # Assertion 2: caught by parent handler
        caught = False
        try:
            raise CurrencyMismatchError("JPY", "EUR")
        except MihwaError:
            caught = True
        assert caught is True

        # Assertion 3: args contains exactly one string
        assert len(exc.args) == 1
        assert isinstance(exc.args[0], str)


class TestExceptionChaining:
    """Verify that exception __cause__ and args work correctly for custom errors."""

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

    def test_failure_injection_nested_cause_chain(self):
        """Failure injection: chained exceptions through multiple custom error layers."""
        from src.core.exceptions import ExchangeAPIError, MihwaError

        try:
            try:
                raise ValueError("root cause")
            except ValueError as root:
                raise ExchangeAPIError("api layer") from root
        except ExchangeAPIError as api_exc:
            assert api_exc.__cause__ is not None
            assert isinstance(api_exc.__cause__, ValueError)

        # Verify the chain survives a MihwaError-level catch
        caught = False
        try:
            try:
                raise ValueError("root")
            except ValueError as root:
                raise ExchangeAPIError("api") from root
        except MihwaError as m_exc:
            caught = True
            assert m_exc.__cause__ is not None
        assert caught is True
