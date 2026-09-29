"""task-7595(BR-21b) -- OKXAdapter assembly + BR-9 factory registration tests.

Covers: DoD 2 (full-adapter instantiation raises no TypeError), DoD 3
(venue_profile() actually wired, not just the constant defined), DoD 4
(register_exchange_adapter_factory does not touch factory.py's if-branches
-- verified here by exercising the registered path end-to-end rather than
inspecting source text, since a git-diff check belongs to the PR review
step, not a unit test).
"""

from __future__ import annotations

import httpx
import pytest

from src.core.exceptions import FrozenZonePaperAdapterBlockedError
from src.exchanges.factory import (
    LIVE_ADAPTER_ENV,
    UnsupportedExchangeError,
    build_adapter,
    reset_exchange_adapter_factories,
)
from src.exchanges.okx import factory as okx_factory
from src.exchanges.okx.factory import OKXAdapter
from src.exchanges.okx.venue_profile import OKX_SPOT_PROFILE


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(LIVE_ADAPTER_ENV, raising=False)
    reset_exchange_adapter_factories()
    okx_factory.register_exchange_adapter_factory(
        okx_factory.EXCHANGE_NAME, okx_factory._okx_adapter_factory
    )
    yield
    reset_exchange_adapter_factories()


def _adapter(**kwargs) -> OKXAdapter:
    return OKXAdapter("key", "secret", "phrase", demo_mode=True, **kwargs)


# ---- DoD 2: full instantiation raises no TypeError ----


def test_okx_adapter_instantiates_without_typeerror():
    adapter = _adapter()
    assert isinstance(adapter, OKXAdapter)


# ---- DoD 3: venue_profile() is actually wired ----


def test_venue_profile_returns_okx_spot_profile_constant():
    adapter = _adapter()
    assert adapter.venue_profile() is OKX_SPOT_PROFILE


def test_venue_profile_is_not_the_unwired_abc_default():
    """Negative -- reproduces the exact KIS/NH defect this leaf must not
    repeat (ADDING_AN_EXCHANGE.md step 2): calling venue_profile() must not
    fall through to the ABC's default `UnsupportedCapabilityError`."""
    adapter = _adapter()
    try:
        result = adapter.venue_profile()
    except Exception as exc:  # noqa: BLE001 -- explicitly checking it does NOT raise
        pytest.fail(f"venue_profile() unexpectedly raised (unwired defect): {exc!r}")
    assert result.venue == "okx"


# ---- DoD 4 (behavioral proof): BR-9 registration opens "okx" through build_adapter ----


def test_build_adapter_opens_okx_via_registered_factory():
    adapter = build_adapter("okx", "key", "secret", {"api_passphrase": "phrase"})
    assert isinstance(adapter, OKXAdapter)
    assert adapter.is_paper_trading is True


def test_build_adapter_rejects_okx_without_api_passphrase():
    """Negative -- missing the one extra credential field OKX requires."""
    with pytest.raises(UnsupportedExchangeError):
        build_adapter("okx", "key", "secret", None)


def test_build_adapter_still_blocked_by_live_guard_without_env():
    """Negative -- the BR-9 extension point sits behind the fail-closed LIVE
    guard, same as every other exchange (ADDING_AN_EXCHANGE.md step 6)."""
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        build_adapter(
            "okx", "key", "secret", {"api_passphrase": "phrase"}, demo_mode=False
        )


async def test_okx_adapter_live_mode_blocks_trading_calls():
    """Negative -- demo_mode=False must still be blocked by
    `require_paper_sandbox` at the method level (independent third
    defense line), same invariant `test_okx_trading_mixin.py` verifies
    directly on the mixin."""
    adapter = OKXAdapter("key", "secret", "phrase", demo_mode=False)
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await adapter.cancel_order("BTC-USDT:1")


async def test_health_check_returns_false_on_request_failure():
    """Negative -- health_check must collapse a failure to False, not
    propagate an exception to the Watchdog caller."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="down")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://www.okx.com", transport=transport, timeout=10.0
    )
    adapter = OKXAdapter(
        "key",
        "secret",
        "phrase",
        demo_mode=True,
        http_client=http_client,
        sleep_fn=lambda s: _noop(),
    )
    assert await adapter.health_check() is False


async def _noop() -> None:
    return None
