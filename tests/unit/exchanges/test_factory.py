"""src/exchanges/factory.py — build_adapter() 레지스트리 단위테스트.

PM 배정 지침 ③ — "nh" 등록 검증(기존 bitget/kis 분기는 이 세션이
새로 만든 게 아니라 회귀 범위 밖).
"""
import pytest

from src.exchanges.factory import SUPPORTED_EXCHANGES, UnsupportedExchangeError, build_adapter
from src.exchanges.nh.adapter import NHAdapter


def test_nh_registered_in_supported_exchanges():
    assert "nh" in SUPPORTED_EXCHANGES


def test_build_adapter_nh_requires_act_no():
    with pytest.raises(UnsupportedExchangeError):
        build_adapter("nh", "key", "secret", extra=None)


def test_build_adapter_nh_returns_nh_adapter():
    adapter = build_adapter("nh", "key", "secret", extra={"act_no": "1234567890"})

    assert isinstance(adapter, NHAdapter)
    # task-106 재확인 — 모의투자 도메인이 공식 문서상 "미제공"이라
    # 확인되기 전까지 is_paper_trading은 항상 False(adapter.py 참조).
    assert adapter.is_paper_trading is False


def test_build_adapter_unknown_exchange_raises():
    with pytest.raises(UnsupportedExchangeError):
        build_adapter("unknown", "key", "secret", extra=None)
