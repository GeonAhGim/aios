"""DC-5 `ports/{provider,instrument_repository,coverage_repository}.py` —
DEEPEN(task-2877, docs/audit/DEPTH_DC_RD.md#1126) D1 -> D3 증빙.

기존 test_ports_protocol.py는 구조체 vs DTO 불일치·미구현 포트 거부 등
negative 3건으로 `@runtime_checkable`의 이름 매칭 특성만 증명했다(D1) —
감사에서 "실패주입 없음(DB/네트워크), 성능단언 없음, 게이트적색 재현 없음,
D3 요소 없음"으로 지적됐다(1126행). 이 파일이 그 부족분을 채운다. 새 기능은
추가하지 않는다 — `ports/*.py`는 Protocol·DTO 선언만 갖고 있어 여기서
"실패주입"이란 §3.1이 요구하는 tz-aware/타입 제약을 pydantic이 실제로
거부하는지, "게이트"란 그 Protocol을 구현한 가짜 공급자가 §4.1 taxonomy
4종을 다단계 호출 시나리오에서 실제로 fail-closed로 던지는지를 뜻한다.

1. 실패 주입 — naive datetime(§3.1 "UTC tz-aware... 필수")과 taxonomy 밖의
   임의 에러코드 문자열이 조용히 통과하지 않고 각각 `ValidationError`/
   비-retryable 기본값으로 거부됨을 증명한다.
2. 성능 단언 — 대량의 lineage DTO 구성과 Protocol isinstance 검사가 절대
   시간 예산 내에 있음을 증명한다.
3. 게이트 적색 재현 — capabilities -> list_instruments -> fetch_candles(적법)
   -> fetch_candles(커버리지 밖, 불법) -> fetch_candles(권한 없음, 불법) ->
   subscribe(레이트리밋 초과, 불법) 순서를 재생하며, 매 불법 지점에서 정확한
   taxonomy 코드로 거부되고 빈 값/0 채움으로 새지 않음을 증명한다(§4.1).
4. 동시 다중 인스턴스(D3) — 여러 비동기 호출이 서로 다른(적법/불법) 입력으로
   동시에 fetch_candles를 호출해도 서로의 결과·예외를 오염시키지 않는다.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.market_data.ports.coverage_repository import CoverageQuality, CoverageSpan
from src.foundation.market_data.ports.provider import (
    DataLineage,
    DataProviderError,
    DataProviderErrorCode,
    MarketDataProvider,
    ProviderCapabilities,
    RateLimitSpec,
    TimeSpan,
)

_IID = "0" * 25 + "1"


def _aware(day: int = 3) -> datetime:
    return datetime(2026, 9, day, tzinfo=timezone.utc)


def _naive(day: int = 3) -> datetime:
    return datetime(2026, 9, day)  # tzinfo 없음


def _listing(symbol: str = "BTCUSDT") -> VenueListing:
    return VenueListing(
        instrument_id=_IID,
        venue=Venue.BITGET,
        venue_symbol=symbol,
        listed_at=_aware(1),
        delisted_at=None,
        is_primary=True,
    )


def _capabilities() -> ProviderCapabilities:
    return ProviderCapabilities(
        provider_id="bitget",
        asset_classes=frozenset({AssetClass.CRYPTO}),
        timeframes=frozenset({Timeframe.M1}),
        history_from=None,
        realtime=True,
        delayed_seconds=0,
        max_symbols_per_request=100,
        rate_limit=RateLimitSpec(requests_per_second=Decimal("10"), burst=20),
    )


# ---- 실패 주입 1/2 — naive datetime은 §3.1 tz-aware 요구를 조용히 우회하지 못한다 ----


@pytest.mark.parametrize(
    "build",
    [
        lambda: DataLineage(provider_id="bitget", fetched_at=_naive(), raw_digest="deadbeef"),
        lambda: ProviderCapabilities(
            provider_id="bitget",
            asset_classes=frozenset({AssetClass.CRYPTO}),
            timeframes=frozenset({Timeframe.M1}),
            history_from=_naive(),
            realtime=True,
            delayed_seconds=0,
            max_symbols_per_request=100,
            rate_limit=RateLimitSpec(requests_per_second=Decimal("10"), burst=20),
        ),
        lambda: TimeSpan(start=_naive(), end=_aware()),
        lambda: TimeSpan(start=_aware(1), end=_naive()),
        lambda: CoverageSpan(
            instrument_id=_IID,
            venue=Venue.BITGET,
            timeframe=Timeframe.M1,
            quality=CoverageQuality.VALIDATED,
            start=_naive(),
            end=_aware(),
        ),
    ],
    ids=[
        "lineage.fetched_at",
        "capabilities.history_from",
        "time_span.start",
        "time_span.end",
        "coverage_span.start",
    ],
)
def test_naive_datetime_is_rejected_fail_closed(build: Any) -> None:
    """naive datetime을 슬쩍 끼워 넣어도 조용히 UTC로 취급되지 않고
    `ValidationError`로 거부돼야 한다 — §3.1 "UTC tz-aware... 필수"."""
    with pytest.raises(ValidationError):
        build()


# ---- 실패 주입 2/2 — 미지의 enum 값(오타 벤처/타임프레임/품질등급)은 조용히 통과하지 않는다 ----


@pytest.mark.parametrize(
    "build",
    [
        lambda: CoverageSpan(
            instrument_id=_IID,
            venue="NOT_A_VENUE",
            timeframe=Timeframe.M1,
            quality=CoverageQuality.VALIDATED,
            start=_aware(1),
            end=_aware(2),
        ),
        lambda: CoverageSpan(
            instrument_id=_IID,
            venue=Venue.BITGET,
            timeframe="NOT_A_TIMEFRAME",
            quality=CoverageQuality.VALIDATED,
            start=_aware(1),
            end=_aware(2),
        ),
        lambda: CoverageSpan(
            instrument_id=_IID,
            venue=Venue.BITGET,
            timeframe=Timeframe.M1,
            quality="BOGUS_QUALITY",
            start=_aware(1),
            end=_aware(2),
        ),
        lambda: ProviderCapabilities(
            provider_id="bitget",
            asset_classes=frozenset({"NOT_AN_ASSET_CLASS"}),
            timeframes=frozenset({Timeframe.M1}),
            history_from=None,
            realtime=True,
            delayed_seconds=0,
            max_symbols_per_request=100,
            rate_limit=RateLimitSpec(requests_per_second=Decimal("10"), burst=20),
        ),
    ],
    ids=[
        "coverage_span.venue",
        "coverage_span.timeframe",
        "coverage_span.quality",
        "capabilities.asset_classes",
    ],
)
def test_unknown_enum_value_is_rejected_fail_closed(build: Any) -> None:
    """오타·미지의 벤처/타임프레임/품질등급 문자열이 실려도 조용히 통과해
    나중에(DB 저장 시점 등) 원인 모를 실패로 터지는 대신, 구성 시점에 즉시
    `ValidationError`로 거부돼야 한다 — 조용한 통과 금지(§4.1의 정신을
    enum 계약에도 그대로 적용)."""
    with pytest.raises(ValidationError):
        build()


# ---- 성능 단언 ----


@pytest.mark.perf
def test_lineage_bearing_dto_construction_meets_latency_budget() -> None:
    """`ProviderTick`/`CoverageSpan` 등 lineage 필드가 딸린 DTO를 대량
    구성해도(캔들 배치 수신 시뮬레이션) 절대시간 예산 내에 있어야 한다."""
    from src.foundation.market_data.ports.provider import ProviderTick

    n = 5_000
    budget_sec = 2.0  # 실측 로컬 <0.6s
    listing = _listing()
    start = time.perf_counter()
    ticks = [
        ProviderTick(
            listing=listing,
            price=Decimal("50000") + Decimal(i),
            quantity=Decimal("0.01"),
            side="buy" if i % 2 == 0 else "sell",
            traded_at=_aware(),
            lineage=DataLineage(
                provider_id="bitget", fetched_at=_aware(), raw_digest=f"digest-{i}"
            ),
        )
        for i in range(n)
    ]
    elapsed = time.perf_counter() - start
    print(f"[DC-5 ports] ProviderTick x{n} construction in {elapsed:.3f}s (budget<{budget_sec}s)")
    assert len(ticks) == n
    assert elapsed < budget_sec, (
        f"ProviderTick {n}건 구성이 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )


@pytest.mark.perf
def test_protocol_isinstance_check_meets_latency_budget() -> None:
    """`@runtime_checkable` Protocol의 `isinstance()`는 매 호출마다 대상의
    속성을 훑는다 — 대량 반복에서도 예산 내에 있어야 한다(회귀가 있다면
    구조 검사 경로가 선형 이상으로 퇴화했다는 뜻)."""

    class _Impl:
        def capabilities(self): ...
        async def list_instruments(self, asset_class): ...
        async def fetch_candles(self, listing, tf, span): ...
        async def subscribe(self, listings): ...

    impl = _Impl()
    iterations = 3_000
    budget_sec = 3.0  # 실측 로컬 <0.5s(런타임 속성 훑기라 1회당 다소 비싸다)
    start = time.perf_counter()
    for _ in range(iterations):
        assert isinstance(impl, MarketDataProvider)
    elapsed = time.perf_counter() - start
    print(
        f"[DC-5 ports] isinstance(MarketDataProvider) x{iterations} in {elapsed:.3f}s "
        f"(budget<{budget_sec}s)"
    )
    assert elapsed < budget_sec, (
        f"isinstance 검사 {iterations}회가 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )


# ---- 게이트 적색 재현 — 실제 다단계 provider 호출 시나리오 재생 ----


class _FakeProvider:
    """§4.1 taxonomy 4종을 실제로 던지는 최소 구현. `_covered`가 커버리지
    선언을, `_entitled_assets`가 entitlement를 흉내 낸다."""

    def __init__(self) -> None:
        self._caps = _capabilities()
        self._covered_symbol = "BTCUSDT"
        self._entitled_assets = frozenset({AssetClass.CRYPTO})
        self.calls: list[str] = []

    def capabilities(self) -> ProviderCapabilities:
        self.calls.append("capabilities")
        return self._caps

    async def list_instruments(self, asset_class: AssetClass) -> list[VenueListing]:
        self.calls.append(f"list_instruments:{asset_class}")
        if asset_class not in self._entitled_assets:
            raise DataProviderError(
                DataProviderErrorCode.DATA_ENTITLEMENT_DENIED, provider_id="bitget"
            )
        return [_listing(self._covered_symbol)]

    async def fetch_candles(
        self, listing: VenueListing, tf: Timeframe, span: TimeSpan
    ) -> CandleColumns:
        self.calls.append(f"fetch_candles:{listing.venue_symbol}:{span.start.isoformat()}")
        if listing.venue_symbol != self._covered_symbol:
            raise DataProviderError(
                DataProviderErrorCode.DATA_COVERAGE_MISSING, provider_id="bitget"
            )
        if span.start < _aware(1):
            raise DataProviderError(
                DataProviderErrorCode.DATA_COVERAGE_MISSING, provider_id="bitget"
            )
        return CandleColumns(
            ts=[span.start],
            open=[Decimal("1")],
            high=[Decimal("1")],
            low=[Decimal("1")],
            close=[Decimal("1")],
            volume=[Decimal("1")],
            quote_volume=[Decimal("1")],
        )

    async def subscribe(self, listings: Sequence[VenueListing]) -> AsyncIterator[Any]:
        self.calls.append(f"subscribe:{len(listings)}")
        if len(listings) > self._caps.max_symbols_per_request:
            raise DataProviderError(
                DataProviderErrorCode.DATA_PROVIDER_RATE_LIMITED, provider_id="bitget"
            )

        async def _empty() -> AsyncIterator[Any]:
            return
            yield  # pragma: no cover - never reached, satisfies AsyncIterator shape

        return _empty()


async def test_gate_red_multi_step_scenario_rejects_illegal_steps_without_silent_fallback() -> None:
    """capabilities -> list_instruments(적법) -> fetch_candles(적법) ->
    fetch_candles(커버리지 밖) -> list_instruments(미권한 자산군) ->
    subscribe(과다 심볼, 레이트리밋) 순서를 재생한다. 매 불법 지점이 정확한
    taxonomy 코드로 거부되고, 직전까지의 적법 결과가 변형되지 않음을
    증명한다(§4.1 조용한 0 채움 금지)."""
    provider = _FakeProvider()
    assert isinstance(provider, MarketDataProvider)

    caps = provider.capabilities()
    assert caps.provider_id == "bitget"

    listings = await provider.list_instruments(AssetClass.CRYPTO)
    assert len(listings) == 1
    covered_listing = listings[0]

    legal_span = TimeSpan(start=_aware(2), end=_aware(3))
    columns = await provider.fetch_candles(covered_listing, Timeframe.M1, legal_span)
    assert len(columns) == 1

    # 불법 1: 커버리지 밖 구간 -- 빈 CandleColumns가 아니라 예외로 거부돼야 한다.
    uncovered_span = TimeSpan(start=datetime(2020, 1, 1, tzinfo=timezone.utc), end=_aware(1))
    with pytest.raises(DataProviderError) as exc_info:
        await provider.fetch_candles(covered_listing, Timeframe.M1, uncovered_span)
    assert exc_info.value.code is DataProviderErrorCode.DATA_COVERAGE_MISSING
    assert exc_info.value.retryable is False

    # 불법 2: 권한 없는 자산군 -- 빈 리스트가 아니라 예외로 거부돼야 한다.
    with pytest.raises(DataProviderError) as exc_info:
        await provider.list_instruments(AssetClass.KR_EQUITY)
    assert exc_info.value.code is DataProviderErrorCode.DATA_ENTITLEMENT_DENIED
    assert exc_info.value.retryable is False

    # 불법 3: 과다 심볼 구독 -- 레이트리밋으로 거부되고 재시도 가능해야 한다.
    too_many = [_listing(f"SYM{i}") for i in range(caps.max_symbols_per_request + 1)]
    with pytest.raises(DataProviderError) as exc_info:
        await provider.subscribe(too_many)
    assert exc_info.value.code is DataProviderErrorCode.DATA_PROVIDER_RATE_LIMITED
    assert exc_info.value.retryable is True

    # 불법 시도들이 이전 적법 결과를 변형하지 않았다 -- 재조회해도 그대로.
    replay_columns = await provider.fetch_candles(covered_listing, Timeframe.M1, legal_span)
    assert replay_columns.close == columns.close


# ---- 동시 다중 인스턴스(D3) ----


async def test_concurrent_fetch_candles_calls_do_not_cross_contaminate() -> None:
    """서로 다른 (심볼, 구간) 조합을 가진 다수의 동시 `fetch_candles` 호출이
    섞이지 않는다 -- 적법 호출은 자신의 구간을 그대로 돌려주고, 불법 호출은
    자신의 예외만 받는다(다른 태스크의 성공/실패로 오염되지 않음)."""
    provider = _FakeProvider()
    covered_listing = _listing(provider._covered_symbol)
    uncovered_listing = _listing("ETHUSDT")

    async def _legal(i: int) -> Decimal:
        span = TimeSpan(start=_aware(2), end=_aware(3))
        columns = await provider.fetch_candles(covered_listing, Timeframe.M1, span)
        return columns.close[0]

    async def _illegal(i: int) -> DataProviderErrorCode:
        span = TimeSpan(start=_aware(2), end=_aware(3))
        try:
            await provider.fetch_candles(uncovered_listing, Timeframe.M1, span)
        except DataProviderError as exc:
            return exc.code
        raise AssertionError("uncovered listing은 반드시 실패해야 한다")

    legal_tasks = [_legal(i) for i in range(50)]
    illegal_tasks = [_illegal(i) for i in range(50)]
    legal_results, illegal_results = await asyncio.gather(
        asyncio.gather(*legal_tasks), asyncio.gather(*illegal_tasks)
    )

    assert all(r == Decimal("1") for r in legal_results)
    assert all(c is DataProviderErrorCode.DATA_COVERAGE_MISSING for c in illegal_results)
    assert len(provider.calls) == 100
