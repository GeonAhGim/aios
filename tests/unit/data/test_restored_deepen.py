"""DEEPEN(task-10006): src/data 패키지 최상위 계약(asset_class 화이트리스트,
Money 통화 불일치)에 대한 negative/실패주입 보강. 원 리프 task-6704 — 고아
산출물 회수 5828 (qa-2) — 대상."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.exceptions import CurrencyMismatchError
from src.data.models.asset_class import UNKNOWN_ASSET_CLASS, asset_class_for
from src.data.models.base import AssetClass, Currency, Money


def test_asset_class_for_rejects_unknown_symbol_as_unknown_not_silently_bucketed() -> None:
    """화이트리스트에 없는 심볼은 CRYPTO 등으로 조용히 뭉개지지 않고 UNKNOWN."""
    assert asset_class_for("NOTASYMBOL/XYZ") == UNKNOWN_ASSET_CLASS


def test_asset_class_for_rejects_empty_string() -> None:
    assert asset_class_for("") == UNKNOWN_ASSET_CLASS


def test_asset_class_for_rejects_case_variant_of_known_symbol() -> None:
    """화이트리스트는 대소문자를 정규화하지 않는다 — 변형은 UNKNOWN."""
    assert asset_class_for("btc/usdt") == UNKNOWN_ASSET_CLASS


def test_asset_class_for_accepts_whitelisted_symbol() -> None:
    assert asset_class_for("BTC/USDT") == AssetClass.CRYPTO.value


def test_money_add_rejects_currency_mismatch() -> None:
    usdt = Money(amount=Decimal("10"), currency=Currency.USDT)
    krw = Money(amount=Decimal("10"), currency=Currency.KRW)
    with pytest.raises(CurrencyMismatchError):
        usdt + krw


def test_money_add_same_currency_succeeds() -> None:
    a = Money(amount=Decimal("1.5"), currency=Currency.USDT)
    b = Money(amount=Decimal("2.5"), currency=Currency.USDT)
    result = a + b
    assert result.amount == Decimal("4.0")
    assert result.currency == Currency.USDT


def test_asset_class_for_lookup_failure_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    """의존 딕셔너리 조회 자체가 예외를 던지는 상황(실패주입) — fail-closed 확인:
    asset_class_for는 예외를 삼켜 CRYPTO 등으로 위장하지 않고 그대로 전파해야 한다."""
    import src.data.models.asset_class as asset_class_module

    class _ExplodingLookup:
        def get(self, key: str, default: AssetClass | None = None) -> AssetClass | None:
            raise RuntimeError("injected dependency failure")

    monkeypatch.setattr(asset_class_module, "_SYMBOL_ASSET_CLASS", _ExplodingLookup(), raising=True)
    with pytest.raises(RuntimeError, match="injected dependency failure"):
        asset_class_module.asset_class_for("BTC/USDT")
