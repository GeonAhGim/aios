"""OKXTradingMixin MARKET 주문의 min_notional 참조가(reference price)
검증 — task-8357(실제 notional 계산)/task-8374(스테일/0·NaN 거부)/
task-8454(스테일니티 검사의 벽시계 의존 제거) 계열.

`tests/exchanges/okx/test_okx_trading_mixin.py`에서 분리했다(ADR-2026-09-10-C
§7, loc_over_500 -- 참조가 신선도/유효성 검증은 주문 생성·취소·정정 일반
경로와 독립적인 변경축이라 별도 파일이 책임을 더 명확히 드러낸다). 스텁
클라이언트(`_StubClient`)와 헬퍼(`_paper_client`/`_order`/
`_ok_order_response`)는 그대로 재사용한다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.core.exceptions import ExchangeAPIError, FatalExchangeError
from src.data.models.trading import OrderStatus, OrderType
from tests.exchanges.okx.test_okx_trading_mixin import (
    _ok_order_response,
    _order,
    _paper_client,
)

pytestmark = pytest.mark.asyncio


async def test_place_order_rejects_market_order_when_reference_price_fetch_fails():
    """부정 테스트 + 장애주입(task-8357, CTO 결정): 참조가 조회
    (get_ticker)가 실패하면 근사치로 되돌아가거나 검증을 건너뛰지 않고
    fail-closed로 주문 자체를 거부해야 한다."""
    client = _paper_client(ticker_error=ExchangeAPIError("price feed 장애"))
    order = _order(order_type=OrderType.MARKET).model_copy(update={"quantity": Decimal("1")})
    with pytest.raises(FatalExchangeError):
        await client.place_order(order)
    assert client.calls == []


async def test_place_order_rejects_market_order_with_stale_reference_price():
    """부정 테스트(task-8374, finding#223 후속): 5초 초과로 스테일한
    참조가는 notional이 충분해도(50000*0.1=5000) 신뢰하지 않고 거부."""
    client = _paper_client(ticker_price=Decimal("50000"), ticker_age=timedelta(seconds=6))
    order = _order(order_type=OrderType.MARKET).model_copy(update={"quantity": Decimal("0.1")})
    with pytest.raises(FatalExchangeError):
        await client.place_order(order)
    assert client.calls == []


async def test_place_order_rejects_market_order_with_zero_reference_price():
    """부정 테스트(task-8374): 참조가 0은 notional을 무조건 0으로 만들어
    min_notional 검증을 무의미하게 통과시키므로 계산 전에 거부."""
    client = _paper_client(ticker_price=Decimal("0"))
    order = _order(order_type=OrderType.MARKET).model_copy(update={"quantity": Decimal("1")})
    with pytest.raises(FatalExchangeError):
        await client.place_order(order)
    assert client.calls == []


async def test_place_order_rejects_market_order_with_negative_reference_price():
    """부정 테스트(task-8374): 음수 참조가(데이터 소스 이상)도 거부 --
    0 검사만으로는 걸러지지 않는 별도 경로."""
    client = _paper_client(ticker_price=Decimal("-1"))
    order = _order(order_type=OrderType.MARKET).model_copy(update={"quantity": Decimal("1")})
    with pytest.raises(FatalExchangeError):
        await client.place_order(order)
    assert client.calls == []


async def test_place_order_accepts_market_order_with_reference_price_just_under_staleness_limit():
    """회귀 방지(QA task-8433): 스테일 거부(6초) 테스트만 있고 경계 통과가
    없었다 -- 임계값(5초) 바로 아래(4.9초, 정각은 실행시간 오차로 피함)는
    통과함을 증명한다."""
    client = _paper_client(
        ticker_price=Decimal("50000"),
        ticker_age=timedelta(milliseconds=4900),
        responses={"/api/v5/trade/order": _ok_order_response()},
    )
    order = _order(order_type=OrderType.MARKET).model_copy(update={"quantity": Decimal("0.1")})
    result = await client.place_order(order)
    assert result.exchange_order_id == "BTC-USDT:1234567"


async def test_market_order_staleness_check_uses_injectable_clock_not_real_elapsed_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """회귀 테스트(task-8454, CI red 근본 원인 정정) -- task-8374(commit
    aadec6bf)가 추가한 스테일니티 검사는 `datetime.now(timezone.utc)`를
    인라인으로 읽었다. 이 저장소의 다른 모든 신선도/만료 검사
    (`core/security/break_glass.py:mfa_step_up_fresh`,
    `foundation/ai/factory/application/promote_to_paper.py`의 `clock` 인자
    등)는 예외 없이 주입 가능한 `now`/`clock`을 받는데, 이 한 곳만 실제
    벽시계를 직접 읽어 CI처럼 워커 13개+llama.cpp가 동시에 도는 경합
    머신에서 ticker 조회~검증 사이 실제 경과 시간이 우연히 5초를 넘으면
    스테일하지 않은 참조가도 거짓 거부됐다(간헐적 CI red의 근본 원인).
    `_utcnow()`로 간접화한 뒤에는 실제 sleep 없이 `_utcnow`만
    monkeypatch해 스테일 판정을 결정적으로 재현할 수 있어야 한다."""
    from src.exchanges.okx import trading_mixin as okx_trading_mixin

    client = _paper_client(ticker_price=Decimal("50000"), ticker_age=timedelta(seconds=0))
    order = _order(order_type=OrderType.MARKET).model_copy(update={"quantity": Decimal("0.1")})

    real_utcnow = okx_trading_mixin._utcnow
    # 실제 sleep 없이, 검증 시점의 논리 시각만 6초 미래로 이동시켜
    # "ticker 조회 후 검증까지 6초가 걸린" 경합 상황을 결정적으로 흉내낸다.
    monkeypatch.setattr(okx_trading_mixin, "_utcnow", lambda: real_utcnow() + timedelta(seconds=6))
    with pytest.raises(FatalExchangeError, match="스테일"):
        await client.place_order(order)
    assert client.calls == []


async def test_market_order_not_falsely_rejected_regardless_of_real_test_wall_clock_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """양성 테스트(task-8454) -- `_utcnow()`를 고정하면 신선한 참조가는
    실제 테스트 실행이 (CI 경합으로) 아무리 오래 걸려도 거짓 거부되지
    않는다. 수정 전에는 이 보장이 없었다(실제 벽시계 경과에 직접
    좌우됐다) -- 이것이 간헐적 CI red의 회귀 지점이었다."""
    from src.exchanges.okx import trading_mixin as okx_trading_mixin

    frozen = datetime.now(timezone.utc)
    monkeypatch.setattr(okx_trading_mixin, "_utcnow", lambda: frozen)
    client = _paper_client(ticker_price=Decimal("50000"), ticker_age=timedelta(seconds=0))
    order = _order(order_type=OrderType.MARKET).model_copy(update={"quantity": Decimal("0.1")})
    result = await client.place_order(order)
    assert result.status == OrderStatus.SUBMITTED
