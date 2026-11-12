"""src/exchanges/factory.py — build_adapter() 레지스트리 단위테스트.

PM 배정 지침 ③ — "nh" 등록 검증(기존 bitget/kis 분기는 이 세션이
새로 만든 게 아니라 회귀 범위 밖).
"""

import pytest

from src.exchanges.factory import (
    SUPPORTED_EXCHANGES,
    PaperSimAdapterUnavailableError,
    UnsupportedExchangeError,
    build_adapter,
    register_paper_sim_factory,
    reset_paper_sim_factory,
)
from src.exchanges.nh.adapter import NHAdapter


@pytest.fixture(autouse=True)
def _reset_paper_sim_factory(monkeypatch: pytest.MonkeyPatch):
    """각 테스트 전 paper_sim 팩토리를 리셋 — 다른 테스트의 등록이
    이 테스트 파일의 단언에 영향을 주지 않도록 한다."""
    monkeypatch.delenv("AIOS_ALLOW_LIVE_ADAPTER", raising=False)
    reset_paper_sim_factory()
    yield
    reset_paper_sim_factory()


def test_nh_registered_in_supported_exchanges():
    assert "nh" in SUPPORTED_EXCHANGES


def test_build_adapter_nh_requires_act_no():
    """negative — NH 거래소는 extra에 act_no가 필수, 누르면 UnsupportedExchangeError."""
    with pytest.raises(UnsupportedExchangeError):
        build_adapter("nh", "key", "secret", extra=None)


def test_build_adapter_nh_returns_nh_adapter():
    adapter = build_adapter("nh", "key", "secret", extra={"act_no": "1234567890"})

    assert isinstance(adapter, NHAdapter)
    # task-106 재확인 — 모의투자 도메인이 공식 문서상 "미제공"이라
    # 확인되기 전까지 is_paper_trading은 항상 False(adapter.py 참조).
    assert adapter.is_paper_trading is False


def test_build_adapter_unknown_exchange_raises():
    """negative — 미지원 거래소 이름으로 호출하면 UnsupportedExchangeError."""
    with pytest.raises(UnsupportedExchangeError):
        build_adapter("unknown", "key", "secret", extra=None)


def test_build_adapter_bitget_missing_passphrase_raises():
    """negative — Bitget은 extra에 api_passphrase가 필수, 누르면 UnsupportedExchangeError."""
    with pytest.raises(UnsupportedExchangeError):
        build_adapter("bitget", "key", "secret", extra={})


def test_build_adapter_kis_missing_required_fields_raises():
    """negative — KIS는 extra에 cano/acnt_prdt_cd가 필수, 누르면 UnsupportedExchangeError."""
    with pytest.raises(UnsupportedExchangeError):
        build_adapter("kis", "key", "secret", extra={"cano": "12345678"})
    with pytest.raises(UnsupportedExchangeError):
        build_adapter("kis", "key", "secret", extra={"acnt_prdt_cd": "01"})
    with pytest.raises(UnsupportedExchangeError):
        build_adapter("kis", "key", "secret", extra={})


def test_build_adapter_paper_sim_without_registered_factory_raises():
    """negative — paper_sim은 등록 전에는 PaperSimAdapterUnavailableError를 던진다."""
    with pytest.raises(PaperSimAdapterUnavailableError):
        build_adapter("paper_sim", "key", "secret", extra=None)


def test_build_adapter_paper_sim_after_register_returns_adapter():
    """positive — paper_sim 팩토리를 등록하면 어댑터를 생성한다."""

    class _DummyAdapter:
        is_paper_trading = True
        is_sandboxed = True

    register_paper_sim_factory(lambda k, s, e: _DummyAdapter())
    adapter = build_adapter("paper_sim", "key", "secret", extra={})

    assert isinstance(adapter, _DummyAdapter)
    assert adapter.is_paper_trading is True
