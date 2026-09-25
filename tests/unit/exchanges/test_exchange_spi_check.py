"""BR-18(task-6696) — `scripts/check_exchange_spi.py`의 SPI 준수·capability
교차검증 게이트 자체를 검증한다.

Spec 근거: ADR-2026-09-06-I("브로커 우선, 100% 커버리지"),
ADR-2026-09-24-A Decision 5, docs/exchanges/ADDING_AN_EXCHANGE.md.

검증 축:
1. 실제 등록된 4개 거래소(bitget/kis/nh + paper_sim 드라이런)가 위반 0건으로
   통과한다(그린 스냅샷).
2. negative — 추상 메서드 누락/venue_profile 배선 누락/capability-구현
   불일치를 각각 별도 스텁으로 주입해 게이트가 잡아내는지 확인한다.
3. failure-injection — KIS 어댑터의 `venue_profile()`을 런타임에 ABC
   기본값으로 되돌려(BR-18에서 실제로 있었던 결함 재현) 게이트가 그
   회귀를 잡아내는지 확인한다(red-gate reproduction, `main()`이 exit 1을
   내는 것까지 포함).
4. 수치 성능 단언(D2) — 검사 자체는 순수 클래스 속성 비교라 I/O가 없어야
   한다.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from scripts.check_exchange_spi import (
    AdapterEntry,
    _check_entry,
    adapter_matrix,
    check,
    main,
    spi_surface,
)
from src.exchanges.common.adapter import ExchangeAdapter
from src.exchanges.kis.adapter import KISAdapter
from src.exchanges.kis.venue_profile import KIS_KR_EQUITY_PROFILE


class _MinimalAdapter(ExchangeAdapter):
    """추상 메서드 14종만 구현한 최소 스텁(호출은 안 함)."""

    @property
    def is_paper_trading(self) -> bool:
        return True

    @property
    def is_sandboxed(self) -> bool:
        return True

    def get_capabilities(self) -> Any:  # pragma: no cover
        raise AssertionError

    async def get_ticker(self, symbol: str) -> Any:  # pragma: no cover
        raise AssertionError

    async def get_orderbook(self, symbol: str, depth: int = 20) -> Any:  # pragma: no cover
        raise AssertionError

    async def get_ohlcv(
        self, symbol: str, timeframe: str, limit: int = 100
    ) -> Any:  # pragma: no cover
        raise AssertionError

    async def subscribe_ticker_stream(self, symbol: str, callback: Any) -> None:  # pragma: no cover
        raise AssertionError

    async def get_balance(self, asset: str | None = None) -> Any:  # pragma: no cover
        raise AssertionError

    async def get_positions(self, symbol: str | None = None) -> Any:  # pragma: no cover
        raise AssertionError

    async def get_order(self, order_id: str) -> Any:  # pragma: no cover
        raise AssertionError

    async def place_order(self, order: Any) -> Any:  # pragma: no cover
        raise AssertionError

    async def cancel_order(self, order_id: str) -> bool:  # pragma: no cover
        raise AssertionError

    async def modify_order(self, order_id: str, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError

    async def health_check(self) -> bool:  # pragma: no cover
        raise AssertionError


class _WiredAdapter(_MinimalAdapter):
    """venue_profile()을 올바르게 배선한 "정상" 스텁 — 대조군."""

    def venue_profile(self):
        return KIS_KR_EQUITY_PROFILE


# ---------------------------------------------------------------------------
# 1. 그린 스냅샷 — 실제 등록 거래소 4개(paper_sim 드라이런 포함)
# ---------------------------------------------------------------------------


def test_registered_adapters_pass_with_zero_violations():
    violations = check()
    assert violations == []


def test_spi_surface_includes_l4_13_optional_methods():
    surface = spi_surface()
    for name in (
        "get_open_orders",
        "get_fills",
        "find_order_by_client_id",
        "venue_profile",
        "subscribe_order_stream",
    ):
        assert name in surface


# ---------------------------------------------------------------------------
# 2. negative — 세 가지 위반 유형을 각각 별도 스텁으로 주입
# ---------------------------------------------------------------------------


def test_negative_missing_abstract_method_is_flagged():
    """스텁 클래스가 추상 메서드를 하나라도 빼먹으면(여기서는 클래스 자체를
    만들지 않고 __abstractmethods__를 직접 비교) 위반으로 잡힌다."""

    class _BrokenAdapter(_MinimalAdapter):
        pass

    # health_check을 다시 추상으로 만들어(실수로 인터페이스만 선언하고
    # 구현을 지운 상황을 흉내) __abstractmethods__가 비지 않게 만든다.
    _BrokenAdapter.health_check = ExchangeAdapter.health_check
    _BrokenAdapter.__abstractmethods__ = frozenset({"health_check"})

    entry = AdapterEntry("broken", _BrokenAdapter, KIS_KR_EQUITY_PROFILE)
    violations = _check_entry(entry)

    assert any("추상 메서드 미구현" in v.reason for v in violations)


def test_negative_unwired_venue_profile_is_flagged():
    """BR-18에서 실제로 있었던 결함 — venue_profile.py에 상수는 있는데
    adapter.venue_profile()이 ABC 기본값에 머무는 경우."""
    entry = AdapterEntry("unwired", _MinimalAdapter, KIS_KR_EQUITY_PROFILE)

    violations = _check_entry(entry)

    assert any("배선 누락" in v.reason for v in violations)


def test_negative_ws_orders_capability_mismatch_is_flagged():
    """supports_ws_orders=True인데 subscribe_order_stream이 ABC 기본값이면
    capability 선언과 구현이 모순이다."""
    mismatched_profile = KIS_KR_EQUITY_PROFILE.model_copy(update={"supports_ws_orders": True})
    entry = AdapterEntry("mismatched", _WiredAdapter, mismatched_profile)

    violations = _check_entry(entry)

    assert any("capability 선언과 구현 불일치" in v.reason for v in violations)


def test_positive_wired_adapter_with_matching_capability_has_no_violations():
    """대조군 — venue_profile 배선 + supports_ws_orders=False(선언과 일치)면
    위반이 없다."""
    entry = AdapterEntry("wired", _WiredAdapter, KIS_KR_EQUITY_PROFILE)
    assert _check_entry(entry) == []


# ---------------------------------------------------------------------------
# 3. failure-injection / red-gate reproduction — 실제 KIS 결함을 재현
# ---------------------------------------------------------------------------


def test_failure_injection_reintroducing_kis_bug_is_caught(monkeypatch: pytest.MonkeyPatch):
    """BR-18에서 고친 KIS의 실제 결함(venue_profile 미배선)을 런타임에
    다시 주입해도 게이트가 잡아내는지 확인 — 회귀가 조용히 통과할 수 없다."""
    monkeypatch.setattr(KISAdapter, "venue_profile", ExchangeAdapter.venue_profile)

    entry = AdapterEntry("kis", KISAdapter, KIS_KR_EQUITY_PROFILE)
    violations = _check_entry(entry)

    assert any("배선 누락" in v.reason for v in violations)


def test_red_gate_reproduction_main_exits_1_when_kis_regresses(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """적색 게이트 재현(D3 계열 요구) — 실제 `main()`이 이 회귀 상태에서
    exit code 1을 내고, 위반 사유를 표준출력에 남기는지까지 확인한다."""
    monkeypatch.setattr(KISAdapter, "venue_profile", ExchangeAdapter.venue_profile)

    exit_code = main()
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "kis" in captured.out
    assert "배선 누락" in captured.out


# ---------------------------------------------------------------------------
# 4. 수치 성능 단언(D2) — 순수 클래스 속성 비교라 I/O가 없어야 한다
# ---------------------------------------------------------------------------


def test_check_matrix_has_bounded_latency_for_repeated_runs():
    """검사 로직이 실계좌/네트워크 I/O 없이 순수 클래스 속성 비교만 하므로,
    실제 4개 어댑터 행렬을 200회 반복 실행해도 벽시계 지연이 명시적 상한
    (0.5s) 이내여야 한다. 여기에 실수로 인스턴스화나 원격 조회가 섞이면
    상한을 넘겨 적색이 된다."""
    started = time.perf_counter()
    for _ in range(200):
        entries = adapter_matrix()
        for entry in entries:
            _check_entry(entry)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5
