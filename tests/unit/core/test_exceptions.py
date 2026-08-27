import pytest

from src.core.exceptions import (
    CurrencyMismatchError,
    ExchangeAPIError,
    FatalExchangeError,
    MihwaError,
    RetryableExchangeError,
    ZoneViolationError,
)


def test_all_custom_errors_inherit_mihwa_error():
    for cls in (
        CurrencyMismatchError,
        ExchangeAPIError,
        FatalExchangeError,
        RetryableExchangeError,
        ZoneViolationError,
    ):
        assert issubclass(cls, MihwaError)


def test_currency_mismatch_message_includes_both_currencies():
    err = CurrencyMismatchError("USDT", "KRW")
    assert "USDT" in str(err)
    assert "KRW" in str(err)


def test_mihwa_error_not_caught_by_unrelated_exception_subclass():
    with pytest.raises(MihwaError):
        raise ExchangeAPIError("boom")
