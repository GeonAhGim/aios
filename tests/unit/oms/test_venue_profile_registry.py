"""L4-04 — production `SymbolRegistry` wiring + verified-snapshot guard. DB 없음.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-04 DoD (a)(c).

착수 시 재현: `rg -n "SymbolRegistry\\(" src --glob '!tests'`가 0건이었다
(레지스트리 타입/`register()`는 있었지만 어디서도 실제로 인스턴스화되지
않았다) — `wiring.build_production_symbol_registry()`가 그 유일한 production
인스턴스화 지점이 된다(이 파일이 그것을 증명한다).
"""
from __future__ import annotations

import pytest

from src.exchanges.bitget.venue_profile import BITGET_SYMBOL_SNAPSHOTS
from src.exchanges.kis.venue_profile import KIS_SYMBOL_SNAPSHOTS
from src.exchanges.nh.venue_profile import NH_SYMBOL_SNAPSHOTS
from src.services.oms.application.wiring import build_production_symbol_registry
from src.services.oms.domain.errors import OrderValidationError, UnknownSymbolError
from src.services.oms.domain.rounding import require_verified
from src.services.oms.domain.symbol_registry import SymbolSnapshot


def test_production_registry_resolves_real_symbols_per_venue() -> None:
    registry = build_production_symbol_registry()

    assert registry.to_venue("BTC/USDT", "bitget") == "BTCUSDT"
    assert registry.to_venue("ETH/USDT", "bitget") == "ETHUSDT"
    assert registry.to_venue("005930.KS", "kis") == "005930"
    assert registry.to_venue("005930.KS", "nh") == "005930"


def test_production_registry_unregistered_symbol_fails_closed() -> None:
    """DoD (b) — 미등록 심볼(ZZZUSDT)은 등록된 어떤 venue에도 없다."""
    registry = build_production_symbol_registry()

    with pytest.raises(UnknownSymbolError):
        registry.to_venue("ZZZUSDT", "bitget")


def test_production_registry_symbol_is_venue_scoped() -> None:
    """R8 — bitget에 등록된 캐노니컬이라도 kis에는 등록되지 않았다."""
    registry = build_production_symbol_registry()

    with pytest.raises(UnknownSymbolError):
        registry.to_venue("BTC/USDT", "kis")


@pytest.mark.parametrize(
    ("snapshots", "canonical"),
    [
        (KIS_SYMBOL_SNAPSHOTS, "005930.KS"),
        (NH_SYMBOL_SNAPSHOTS, "005930.KS"),
    ],
)
def test_kr_equity_snapshots_are_honestly_unverified(
    snapshots: dict[str, SymbolSnapshot], canonical: str
) -> None:
    """이 세션에서 KRX 가격대를 실시간 조회하지 않았으므로 KIS/NH 스냅샷은
    verified=False여야 한다 — 조용히 검증된 것처럼 표기하면 안 된다."""
    assert snapshots[canonical].verified is False


def test_bitget_btc_usdt_snapshot_is_verified() -> None:
    """bitget BTC/USDT는 실제 공개 엔드포인트 응답에서 옮긴 값(L4-30 확인)."""
    assert BITGET_SYMBOL_SNAPSHOTS["BTC/USDT"].verified is True


def test_require_verified_passes_through_verified_spec() -> None:
    registry = build_production_symbol_registry()
    spec = registry.spec("BTC/USDT", "bitget")

    assert require_verified(spec) is spec


def test_require_verified_rejects_unverified_spec_adversarial() -> None:
    """DoD (c) 적대적 테스트 — verified=False 스냅샷 값으로 라운딩/최소주문
    판정을 시도하면 조용히 통과하지 않고 명시적으로 거부된다."""
    registry = build_production_symbol_registry()
    registry.register_snapshot("005930.KS", "kis", KIS_SYMBOL_SNAPSHOTS["005930.KS"])
    spec = registry.spec("005930.KS", "kis")
    assert spec.verified is False

    with pytest.raises(OrderValidationError) as excinfo:
        require_verified(spec)

    assert excinfo.value.code == "OMS_VALIDATION_UNVERIFIED_SPEC"
