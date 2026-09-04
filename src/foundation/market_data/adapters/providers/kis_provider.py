"""DC-12 — KIS(한국투자증권) `MarketDataProvider` SPI 위임 어댑터.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2 모듈표 50행, §9.2 DC-12(선행 DC-11, task-1187 b0b8bed 머지 완료).

`BitgetProvider`(`bitget_provider.py`)와 같은 패턴 — 새 거래소 클라이언트를
만들지 않고 기존 `src.exchanges.kis.KISAdapter`를 생성자로 주입받아 호출을
위임한다. `src/exchanges/**`는 이 리프에서 한 줄도 고치지 않는다
(task-1211 decision).

스콥 — `KISAdapter.get_capabilities()`가 Phase 1 capability-gated 원칙에
따라 `KR_EQUITY`만 선언하듯(해외주식/선물옵션은 Draft), 이 SPI 계층도
`Venue.KIS_KRX`(국내주식) 하나만 다룬다. `KISMarketDataMixin.get_ohlcv`가
실제로 지원하는 timeframe도 일봉(`1d`)·분봉(`1m`) 둘뿐이라(02d 스펙 §2,
그 외 분봉은 거래소가 직접 주지 않음) `capabilities().timeframes`도 그
둘만 선언한다 — 지원하지 않는 것처럼 보이는 timeframe을 미리 선언해
capability-gated 원칙(§2.0-A)을 어기지 않는다.

미검증(외부 문서 대조 전, 성공으로 위장하지 않음):
- `history_from`: KIS 일봉 조회가 `FID_INPUT_DATE_1="19000101"`을 보내긴
  하지만 실제 서버가 그만큼 과거 데이터를 보유하는지는 확인하지 않았다.
  임의 날짜를 채우면 §4.1 위반이므로 `None`(모름)으로 둔다.
- `rate_limit`: KIS 공식 문서 초당 한도는 라이브 검증 전까지 보수적
  추정치(초당 15건, burst 15)를 쓴다.
- 구간 조회 한계: `KISMarketDataMixin.get_ohlcv`는 `[start, end)` 구간
  파라미터를 받지 않는다(existing adapter가 "최신부터 `limit`개"만 지원,
  일봉의 경우 1900년부터 오늘까지를 조회하되 응답을 `limit`으로 자를
  뿐이다). 그래서 이 provider는 그 결과를 받아 `span`으로 사후 필터링만
  한다 — 어댑터가 애초에 닿지 못하는 과거 구간은 정직하게
  `DATA_COVERAGE_MISSING`이 된다(§4.1, 0 채움 아님).

`list_instruments`/`subscribe`는 이 리프의 구현 대상이 아니다(task-1211
decision, `BitgetProvider`와 동일 근거 — instrument_id(ULID) 발급은 DC-2
소관, 실시간 스트림 배선은 DC-17 소관). 둘 다 `NotImplementedError`로
fail-closed 한다.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from decimal import Decimal

from src.data.models.base import AssetClass
from src.exchanges.kis.adapter import KISAdapter
from src.foundation.market_data.adapters.providers.base_adapter import BaseProviderAdapter
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.market_data.domain.reference.symbol_normalizer import to_canonical
from src.foundation.market_data.ports.provider import (
    DataProviderError,
    DataProviderErrorCode,
    ProviderCapabilities,
    RateLimitSpec,
    TickOrCandle,
    TimeSpan,
)

__all__ = ["KISProvider"]

_MAX_CANDLES_PER_REQUEST = 100  # KISMarketDataMixin.get_ohlcv 기본값과 동일.
_SUPPORTED_TIMEFRAMES = frozenset({Timeframe.M1, Timeframe.D1})

_CAPABILITIES = ProviderCapabilities(
    provider_id="kis",
    asset_classes=frozenset({AssetClass.KR_EQUITY}),
    timeframes=_SUPPORTED_TIMEFRAMES,
    history_from=None,  # 미검증(문서 미대조) — 임의 날짜로 채우지 않는다.
    realtime=True,  # KISAdapter.get_capabilities().supports_websocket
    delayed_seconds=0,
    max_symbols_per_request=1,  # REST 조회 엔드포인트는 심볼 1개씩만 조회.
    rate_limit=RateLimitSpec(requests_per_second=Decimal(15), burst=15),  # 미검증
)


class KISProvider(BaseProviderAdapter):
    """`KISAdapter`(기존 `src/exchanges/kis`)에 위임하는 `MarketDataProvider`
    (DC-5) 구현체. 국내주식(`Venue.KIS_KRX`) 전용(Phase 1 capability-gated
    범위, 위 모듈 docstring 참고)."""

    def __init__(self, adapter: KISAdapter, **kwargs: object) -> None:
        super().__init__(_CAPABILITIES, **kwargs)  # type: ignore[arg-type]
        self._adapter = adapter

    def capabilities(self) -> ProviderCapabilities:
        return _CAPABILITIES

    async def list_instruments(self, asset_class: AssetClass) -> list[VenueListing]:
        raise NotImplementedError(
            "KISProvider.list_instruments: DC-12 스콥 밖 — instrument_id(ULID) "
            "발급은 DC-2 심볼 마스터 소관이며 이 SPI 계층은 그 저장소를 참조하지 "
            "않는다(task-1211 decision)."
        )

    async def fetch_candles(
        self, listing: VenueListing, tf: Timeframe, span: TimeSpan
    ) -> CandleColumns:
        if listing.venue is not Venue.KIS_KRX:
            raise ValueError(
                f"KISProvider는 Venue.KIS_KRX listing만 처리한다: {listing.venue!r}"
            )
        if tf not in _SUPPORTED_TIMEFRAMES:
            raise ValueError(
                f"KISProvider는 {sorted(t.value for t in _SUPPORTED_TIMEFRAMES)}만 "
                f"지원한다: {tf.value!r}"
            )
        symbol = to_canonical(Venue.KIS_KRX, listing.venue_symbol)

        async def _op() -> CandleColumns:
            raw_candles = await self._adapter.get_ohlcv(
                symbol, tf.value, limit=_MAX_CANDLES_PER_REQUEST
            )
            in_span = sorted(
                (c for c in raw_candles if span.start <= c.open_time < span.end),
                key=lambda c: c.open_time,
            )
            if not in_span:
                raise DataProviderError(
                    DataProviderErrorCode.DATA_COVERAGE_MISSING,
                    provider_id=self._provider_id,
                    message=(
                        f"kis: {symbol} {tf.value} 구간 [{span.start}, {span.end}) "
                        "데이터 없음"
                    ),
                )
            return CandleColumns(
                ts=[c.open_time for c in in_span],
                open=[c.open for c in in_span],
                high=[c.high for c in in_span],
                low=[c.low for c in in_span],
                close=[c.close for c in in_span],
                volume=[c.volume for c in in_span],
                quote_volume=[None for _ in in_span],
            )

        return await self.call_with_retry(_op)

    async def subscribe(
        self, _listings: Sequence[VenueListing]
    ) -> AsyncIterator[TickOrCandle]:
        raise NotImplementedError(
            "KISProvider.subscribe: DC-12 스콥 밖 — 실시간 스트림 배선은 "
            "DC-17(realtime_fanout) 선행 리프 몫이다(task-1211 decision)."
        )
        yield  # pragma: no cover — mypy가 AsyncIterator 반환형을 추론하도록 하는 도달 불가 표식
