"""R-28 — CandleHistoryCache 단위테스트.

Spec: docs/specs/L4_risk_and_safety_v1.0.md#9 R-28.
DB 없이 순수 캐시 로직만 검증 — adapter는 페이크로 주입한다.

DC-28 후속(ADR-2026-09-06-H D7) — `test_get_isolates_cache_by_owner_id`가
그 DoD("사용자 A의 연결 시세가 사용자 B 응답에 포함되면 테스트가 실패한다")
를 증명한다. 나머지 기존 테스트는 `owner_id`를 명시적으로 넘기도록만
갱신했다(동작 변경 없음).
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.core.loader.risk_policy_loader import RiskPolicy, load_risk_policy
from src.core.risk.decision import RiskOutcome
from src.core.risk.inputs import (
    ActivityInputs,
    EquityInputs,
    ExposureSnapshot,
    OrderIntent,
    RiskInputs,
    SafetyInputs,
    StatsInputs,
)
from src.core.risk.rules.var_es import var_es
from src.data.models.market_data import Candle
from src.services.execution_loop.candle_history import CandleHistoryCache
from src.services.execution_loop.var_estimator import estimate_portfolio_var_es

_OWNER = uuid4()
_RISK_NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


def _make_candles(
    n: int, *, symbol: str = "BTC/USDT", close: Decimal = Decimal("1")
) -> list[Candle]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        Candle(
            symbol=symbol,
            exchange="bitget",
            timeframe="1d",
            open=close,
            high=close,
            low=close,
            close=close,
            volume=Decimal("1"),
            open_time=base + timedelta(days=i),
            close_time=base + timedelta(days=i + 1),
        )
        for i in range(n)
    ]


class _FakeCapabilities:
    def __init__(self, exchange_name: str) -> None:
        self.exchange_name = exchange_name


class _FakeAdapter:
    def __init__(
        self, exchange_name: str = "bitget", *, delay: float = 0.0, marker: Decimal = Decimal("1")
    ) -> None:
        self._exchange_name = exchange_name
        self.calls: list[tuple[str, int]] = []
        self.fail = False
        self._delay = delay
        self._marker = marker

    def get_capabilities(self) -> _FakeCapabilities:
        return _FakeCapabilities(self._exchange_name)

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        self.calls.append((symbol, limit))
        if self._delay:
            await asyncio.sleep(self._delay)
        if self.fail:
            raise RuntimeError("adapter unreachable")
        return _make_candles(limit, symbol=symbol, close=self._marker)


def _risk_inputs(stats: StatsInputs, *, policy: RiskPolicy) -> RiskInputs:
    return RiskInputs(
        tenant_id=uuid4(),
        execution_ref="exec:1",
        certified_badge=True,
        allocated_capital=Decimal("1000"),
        intent=OrderIntent(
            symbol="BTC/USDT",
            asset_class="CRYPTO_SPOT",
            side="BUY",
            quantity=Decimal("0.1"),
            ref_price=Decimal("50000"),
            notional=Decimal("5000"),
            reduce_only=False,
            strategy_id="strat-1",
            strategy_version="1.0",
            capital_pct=Decimal("10"),
        ),
        equity=EquityInputs(as_of=_RISK_NOW),
        exposure=ExposureSnapshot(as_of=_RISK_NOW),
        stats=stats,
        activity=ActivityInputs(),
        safety=SafetyInputs(),
        limits=(),
        as_of=_RISK_NOW,
    )


class _Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


async def test_get_fetches_from_adapter_on_first_call() -> None:
    adapter = _FakeAdapter()
    cache = CandleHistoryCache(now=_Clock(datetime(2026, 1, 1, tzinfo=timezone.utc)))

    result = await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)

    assert result is not None
    assert len(result) == 30
    assert adapter.calls == [("BTC/USDT", 30)]


async def test_get_reuses_cache_within_ttl_for_same_bars() -> None:
    adapter = _FakeAdapter()
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    cache = CandleHistoryCache(now=clock)

    await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)
    clock.advance(59)
    await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)

    assert adapter.calls == [("BTC/USDT", 30)]


async def test_get_refetches_after_ttl_expires() -> None:
    adapter = _FakeAdapter()
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    cache = CandleHistoryCache(now=clock)

    await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)
    clock.advance(60)
    await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)

    assert adapter.calls == [("BTC/USDT", 30), ("BTC/USDT", 30)]


async def test_get_refetches_when_bars_increase_even_within_ttl() -> None:
    adapter = _FakeAdapter()
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    cache = CandleHistoryCache(now=clock)

    await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)
    clock.advance(1)
    result = await cache.get(adapter, "BTC/USDT", bars=60, owner_id=_OWNER)

    assert result is not None
    assert len(result) == 60
    assert adapter.calls == [("BTC/USDT", 30), ("BTC/USDT", 60)]


async def test_get_returns_none_on_adapter_failure_without_prior_cache() -> None:
    adapter = _FakeAdapter()
    adapter.fail = True
    cache = CandleHistoryCache(now=_Clock(datetime(2026, 1, 1, tzinfo=timezone.utc)))

    result = await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)

    assert result is None


async def test_get_returns_none_instead_of_stale_cache_when_refetch_fails() -> None:
    """TTL 만료 후 재조회가 실패하면, 이전에 성공적으로 캐시된(이제는
    만료된) 데이터를 대신 돌려주지 않고 None을 반환해야 한다 — 호출자가
    오래된 캔들을 최신인 것처럼 쓰는 사고를 막는다."""
    adapter = _FakeAdapter()
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    cache = CandleHistoryCache(now=clock)

    first = await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)
    assert first is not None

    clock.advance(60)
    adapter.fail = True
    result = await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)

    assert result is None


async def test_get_caches_per_symbol_independently() -> None:
    adapter = _FakeAdapter()
    cache = CandleHistoryCache(now=_Clock(datetime(2026, 1, 1, tzinfo=timezone.utc)))

    await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)
    await cache.get(adapter, "ETH/USDT", bars=30, owner_id=_OWNER)

    assert adapter.calls == [("BTC/USDT", 30), ("ETH/USDT", 30)]


async def test_get_isolates_cache_by_owner_id() -> None:
    """DC-28 후속(D7) negative — 같은 (exchange, symbol)이라도 `owner_id`가
    다르면 캐시를 공유하지 않는다. 사용자 A의 연결로 가져온 캔들이 캐시
    안에 있어도, 사용자 B의 요청은 같은 항목을 돌려받는 대신 반드시 자기
    자신의 adapter로 다시 조회한다 — 이게 실패하면(호출 1회로 줄면) A의
    연결 시세가 B에게 새는 것과 같은 모양이다."""
    owner_a, owner_b = uuid4(), uuid4()
    adapter_a = _FakeAdapter()
    adapter_b = _FakeAdapter()
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    cache = CandleHistoryCache(now=clock)

    result_a = await cache.get(adapter_a, "AAPL", bars=30, owner_id=owner_a)
    result_b = await cache.get(adapter_b, "AAPL", bars=30, owner_id=owner_b)

    assert adapter_a.calls == [("AAPL", 30)]
    assert adapter_b.calls == [("AAPL", 30)]  # B의 요청이 A의 캐시로 대신되지 않았다.
    assert result_a is not None and result_b is not None
    assert result_a is not result_b


async def test_get_isolates_cache_by_exchange() -> None:
    """negative — 같은 symbol이라도 exchange가 다르면(멀티거래소 상장 동일
    종목) 캐시를 공유하지 않는다. 캐시 키에 exchange_name이 없으면 한
    거래소의 가격 데이터가 다른 거래소 요청 응답으로 새어 들어갈 수 있다."""
    adapter_bitget = _FakeAdapter(exchange_name="bitget")
    adapter_kis = _FakeAdapter(exchange_name="kis")
    cache = CandleHistoryCache(now=_Clock(datetime(2026, 1, 1, tzinfo=timezone.utc)))

    await cache.get(adapter_bitget, "AAPL", bars=30, owner_id=_OWNER)
    await cache.get(adapter_kis, "AAPL", bars=30, owner_id=_OWNER)

    assert adapter_bitget.calls == [("AAPL", 30)]
    assert adapter_kis.calls == [("AAPL", 30)]  # bitget의 캐시로 대신되지 않았다.


async def test_get_propagates_cancelled_error_instead_of_swallowing_it() -> None:
    """negative + 실패주입 — `except Exception`은 `asyncio.CancelledError`
    (BaseException 하위)를 잡지 않는다. 취소를 조용히 삼켜 None으로
    위장하면, 종료(shutdown) 중인 작업이 정상적으로 "데이터 없음"을
    반환한 것처럼 보이는 사고로 이어진다."""
    adapter = _FakeAdapter()
    adapter.fail = True
    cache = CandleHistoryCache(now=_Clock(datetime(2026, 1, 1, tzinfo=timezone.utc)))

    async def _raise_cancelled(*args: object, **kwargs: object) -> list[Candle]:
        raise asyncio.CancelledError()

    adapter.get_ohlcv = _raise_cancelled  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)


async def test_get_cache_hit_makes_no_adapter_call_and_is_fast() -> None:
    """성능 단언(D2) — TTL 이내 재요청은 어댑터를 다시 부르지 않는 O(1)
    dict 조회여야 한다. 500회 캐시 히트가 느슨한 상한(200ms, 회귀 감지용
    — 절대 성능목표로 쓰지 않는다) 안에 끝나면서 어댑터 호출이 여전히
    1회뿐이면 새 I/O를 걷지 않았다는 뜻이다."""
    adapter = _FakeAdapter()
    clock = _Clock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    cache = CandleHistoryCache(now=clock)
    await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)

    started = time.perf_counter()
    for _ in range(500):
        result = await cache.get(adapter, "BTC/USDT", bars=30, owner_id=_OWNER)
        assert result is not None
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    assert adapter.calls == [("BTC/USDT", 30)]  # 501회 요청 중 최초 1회만 실제 I/O.
    assert elapsed_ms < 200.0, f"캐시 히트 500회 지연 회귀 의심: {elapsed_ms:.2f}ms"


async def test_candle_fetch_failure_chain_denies_via_var_es_gate() -> None:
    """게이트 적색 재현(D2) — candle_history.get()이 어댑터 실패로 None을
    반환하면, 실제 하류 소비자(var_estimator.estimate_portfolio_var_es)도
    표본 없음으로 None을 반환하고, 그 None이 실제 var_es 게이트 규칙에
    들어가면 조용히 통과(ALLOW)하지 않고 DENY한다 — candle_history.py의
    docstring이 주장하는 fail-closed 계약(R3)을 체인 전체로 재현한다."""
    adapter = _FakeAdapter()
    adapter.fail = True
    cache = CandleHistoryCache(now=_Clock(datetime(2026, 1, 1, tzinfo=timezone.utc)))

    history = await cache.get(adapter, "BTC/USDT", bars=250, owner_id=_OWNER)
    assert history is None

    # 호출자는 조회 실패한 심볼을 histories dict에서 아예 제외한다(candle_history의 계약).
    histories: dict[str, list[Candle]] = {}
    weights = {"BTC/USDT": Decimal("1")}
    policy = load_risk_policy()

    var_es_result = estimate_portfolio_var_es(histories, weights, policy.var)
    assert var_es_result is None

    stats = StatsInputs(as_of=_RISK_NOW, var_pct=None, es_pct=None, var_method=None, bars_used=None)
    decision = var_es(_risk_inputs(stats, policy=policy), policy)

    assert decision.outcome == RiskOutcome.DENY
    assert decision.missing_fields == ("stats.var_pct",)


async def test_concurrent_gets_do_not_cross_contaminate_owners_under_interleaving() -> None:
    """적대적/동시성 증명(D3) — 두 사용자의 요청이 `asyncio.gather`로 실제
    인터리빙되며(둘 다 어댑터 I/O 대기 중인 순간이 실재) 캐시에 동시
    접근해도, `owner_id`가 키에 있으므로 서로의 캐시 항목을 훔쳐보거나
    덮어쓰지 않는다. 데이터에 서로 다른 마커를 심어, 결과가 뒤섞이면
    (교차 배정) 값 비교에서 바로 드러나게 한다."""
    owner_a, owner_b = uuid4(), uuid4()
    adapter_a = _FakeAdapter(delay=0.01, marker=Decimal("100"))
    adapter_b = _FakeAdapter(delay=0.01, marker=Decimal("200"))
    cache = CandleHistoryCache(now=_Clock(datetime(2026, 1, 1, tzinfo=timezone.utc)))

    result_a, result_b = await asyncio.gather(
        cache.get(adapter_a, "AAPL", bars=5, owner_id=owner_a),
        cache.get(adapter_b, "AAPL", bars=5, owner_id=owner_b),
    )

    assert adapter_a.calls == [("AAPL", 5)]
    assert adapter_b.calls == [("AAPL", 5)]
    assert result_a is not None and result_b is not None
    assert all(c.close == Decimal("100") for c in result_a)
    assert all(c.close == Decimal("200") for c in result_b)
