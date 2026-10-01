"""RTF-03 단위테스트 — `watchdog_process.get_basket_returns`.

docs/RED_TEAM_FINDINGS.md 2026-09-05-44 잔여 갭(`run_one_cycle`이 basket
시세를 조달하는 코드가 없어 `market_wide_correlated`가 영원히 None) 종결의
basket-조달 부분만 떼어 어댑터 없이(DB/네트워크 불필요) 검증한다."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from src.core.safety.watchdog_basket import BASKET_SYMBOLS, get_basket_returns
from src.data.models.market_data import Candle


def _candle(symbol: str, *, open_: str, close: str) -> Candle:
    now = datetime.now(timezone.utc)
    return Candle(
        symbol=symbol,
        exchange="bitget",
        timeframe="5m",
        open=Decimal(open_),
        high=Decimal(open_),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
        open_time=now,
        close_time=now,
    )


class _FakeAdapter:
    def __init__(
        self,
        *,
        candles: dict[str, list[Candle]],
        boom: set[str] | None = None,
        boom_exc: type[BaseException] = RuntimeError,
    ) -> None:
        self._candles = candles
        self._boom = boom or set()
        self._boom_exc = boom_exc
        self.calls: list[tuple[str, str, int]] = []

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        self.calls.append((symbol, timeframe, limit))
        if symbol in self._boom:
            raise self._boom_exc(f"exchange unreachable: {symbol}")
        return self._candles.get(symbol, [])


async def test_computes_percent_return_per_symbol():
    adapter = _FakeAdapter(
        candles={
            "BTC/USDT": [_candle("BTC/USDT", open_="100", close="91")],
            "ETH/USDT": [_candle("ETH/USDT", open_="100", close="103")],
        }
    )
    returns = await get_basket_returns(adapter)
    assert returns["BTC/USDT"] == Decimal("-9")
    assert returns["ETH/USDT"] == Decimal("3")


async def test_uses_the_full_basket_symbol_set():
    adapter = _FakeAdapter(
        candles={s: [_candle(s, open_="100", close="100")] for s in BASKET_SYMBOLS}
    )
    returns = await get_basket_returns(adapter)
    assert set(returns) == set(BASKET_SYMBOLS)


# --- negative -----------------------------------------------------------------


async def test_fetch_failure_drops_symbol_instead_of_faking_zero_move():
    """negative — 한 심볼 조회가 예외를 던지면 그 심볼은 basket에서 빠져야
    한다(0%% 이동으로 오판하면 fail-open이 된다, §R3)."""
    adapter = _FakeAdapter(
        candles={"ETH/USDT": [_candle("ETH/USDT", open_="100", close="97")]},
        boom={"BTC/USDT"},
    )
    returns = await get_basket_returns(adapter)
    assert "BTC/USDT" not in returns
    assert returns["ETH/USDT"] == Decimal("-3")


async def test_empty_candle_list_drops_symbol():
    """negative — 캔들이 아예 없는 응답(빈 리스트)도 조용히 0%%로 채우지
    않고 basket에서 제외한다."""
    adapter = _FakeAdapter(candles={})
    returns = await get_basket_returns(adapter)
    assert returns == {}


async def test_zero_open_price_drops_symbol_instead_of_dividing_by_zero():
    """negative — open==0인 손상 캔들은 ZeroDivisionError를 내며 사이클
    전체를 죽이는 대신 해당 심볼만 basket에서 제외한다."""
    adapter = _FakeAdapter(candles={"BTC/USDT": [_candle("BTC/USDT", open_="0", close="0")]})
    returns = await get_basket_returns(adapter)
    assert returns == {}


async def test_fetches_only_the_single_latest_candle_per_symbol():
    """negative — `limit`을 그 이상으로 넘기면 과거 캔들까지 평균/합산에
    섞여 들어와 최신 이동률이 왜곡될 수 있다. 이 계약(심볼당 최신 1개만
    요청)이 조용히 깨지지 않는지 호출 인자로 검증한다."""
    adapter = _FakeAdapter(
        candles={s: [_candle(s, open_="100", close="100")] for s in BASKET_SYMBOLS}
    )
    await get_basket_returns(adapter)
    assert adapter.calls == [(symbol, "5m", 1) for symbol in BASKET_SYMBOLS]


async def test_extra_candles_beyond_the_first_are_ignored_not_averaged():
    """negative — 어댑터가(계약 위반으로) 여러 캔들을 돌려줘도 두 번째 캔들
    이후는 과거 데이터이므로 섞어 쓰면 안 된다. 리스트의 첫 캔들만 최신으로
    취급해야 한다."""
    stale = _candle("BTC/USDT", open_="100", close="50")
    latest = _candle("BTC/USDT", open_="100", close="91")
    adapter = _FakeAdapter(candles={"BTC/USDT": [latest, stale]})
    returns = await get_basket_returns(adapter)
    assert returns["BTC/USDT"] == Decimal("-9")


# --- 실패 주입 ------------------------------------------------------------------


async def test_all_symbols_failing_returns_empty_basket_not_an_exception():
    """실패 주입 — basket 전체 조회가 실패해도 `get_basket_returns`는 예외를
    전파하지 않고 빈 dict를 돌려준다(호출부 `is_market_wide_move`가 그걸
    `< min_symbols`로 받아 None=조작 의심으로 안전하게 처리한다)."""
    adapter = _FakeAdapter(candles={}, boom=set(BASKET_SYMBOLS))
    returns = await get_basket_returns(adapter)
    assert returns == {}


async def test_non_runtimeerror_fetch_exception_is_still_caught_and_dropped():
    """실패 주입 — `except Exception`(RuntimeError 전용이 아님)이 실제로
    모든 예외 타입을 잡는지, `ValueError` 같은 다른 예외 클래스로 검증한다.
    예외 타입을 좁혀 잡도록 리팩터링되면 이 테스트가 즉시 깨진다."""
    adapter = _FakeAdapter(
        candles={"ETH/USDT": [_candle("ETH/USDT", open_="100", close="105")]},
        boom={"BTC/USDT"},
        boom_exc=ValueError,
    )
    returns = await get_basket_returns(adapter)
    assert "BTC/USDT" not in returns
    assert returns["ETH/USDT"] == Decimal("5")
