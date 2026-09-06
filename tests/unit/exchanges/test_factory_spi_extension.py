"""BR-9(task-1787), ADR-2026-09-06-I D5 — "거래소 확장은 SPI 뒤에서 한다":
`factory.py`의 `if exchange == "bitget"/"kis"/"nh"` 분기를 손대지 않고도
`register_exchange_adapter_factory()`로 새 거래소를 여는지 더미 어댑터로
증명한다.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-B(factory.py 행)
"""
from __future__ import annotations

import pytest

from src.core.exceptions import FrozenZonePaperAdapterBlockedError
from src.exchanges.factory import (
    LIVE_ADAPTER_ENV,
    UnsupportedExchangeError,
    build_adapter,
    register_exchange_adapter_factory,
    reset_exchange_adapter_factories,
)

_DUMMY_EXCHANGE = "dummy_upbit"


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(LIVE_ADAPTER_ENV, raising=False)
    reset_exchange_adapter_factories()
    yield
    reset_exchange_adapter_factories()


def test_unregistered_new_exchange_still_rejected():
    """negative — 등록 전에는 여전히 미지 거래소로 거부된다(무음 통과 없음)."""
    with pytest.raises(UnsupportedExchangeError):
        build_adapter(_DUMMY_EXCHANGE, "key", "secret", None)


def test_dummy_adapter_opens_new_exchange_without_touching_factory_branches():
    """`factory.py` 소스를 한 글자도 바꾸지 않고(이 테스트가 실행 시점에
    호출하는 건 이미 커밋된 `register_exchange_adapter_factory`뿐) 새
    거래소를 열 수 있다는 증명 — D5 DoD."""
    sentinel = object()
    calls: list[tuple[str, str, dict[str, str], bool]] = []

    def _dummy_factory(
        api_key: str, api_secret: str, extra: dict[str, str], demo_mode: bool
    ):
        calls.append((api_key, api_secret, extra, demo_mode))
        return sentinel

    register_exchange_adapter_factory(_DUMMY_EXCHANGE, _dummy_factory)  # type: ignore[arg-type]

    result = build_adapter(_DUMMY_EXCHANGE, "k", "s", {"region": "kr"})

    assert result is sentinel
    assert calls == [("k", "s", {"region": "kr"}, True)]


def test_dummy_adapter_still_blocked_by_live_guard_without_env():
    """negative — 새 거래소도 세 방어선 중 첫 번째(factory LIVE 가드)를
    그대로 통과한다. 확장점이 가드 앞이 아니라 뒤에 있다는 뜻."""
    register_exchange_adapter_factory(
        _DUMMY_EXCHANGE, lambda k, s, e, d: object()  # type: ignore[arg-type, return-value]
    )
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        build_adapter(_DUMMY_EXCHANGE, "k", "s", None, demo_mode=False)


def test_reset_exchange_adapter_factories_restores_explicit_failure():
    register_exchange_adapter_factory(
        _DUMMY_EXCHANGE, lambda k, s, e, d: object()  # type: ignore[arg-type, return-value]
    )
    reset_exchange_adapter_factories()
    with pytest.raises(UnsupportedExchangeError):
        build_adapter(_DUMMY_EXCHANGE, "k", "s", None)
