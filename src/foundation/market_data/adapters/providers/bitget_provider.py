"""DC-12 — Bitget `MarketDataProvider` SPI 위임 어댑터.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2 모듈표 50행, §9.2 DC-12(선행 DC-11, task-1187 b0b8bed 머지 완료).

이 파일은 새 거래소 클라이언트를 만들지 않는다 — 기존 `src.exchanges.bitget.
BitgetAdapter`(REST 인증·서명·재시도는 그쪽 소관)를 생성자로 주입받아
`MarketDataProvider` Protocol(DC-5 `ports/provider.py`, 554f078) 호출로
위임만 한다. `src/exchanges/**`는 이 리프에서 한 줄도 고치지 않는다
(task-1211 decision).

`capabilities()`가 선언하는 값은 `BitgetAdapter.get_capabilities()`
(`ExchangeCapability`, Phase 1 capability-gated 선언)와 어긋나지 않는다 —
거래(trading) 능력 선언과 데이터(data) 능력 선언은 별개 축이지만, 이
어댑터가 실제로 호출할 수 있는 범위(`BitgetMarketDataMixin.get_ohlcv`/
`get_history_candles`가 지원하는 timeframe)를 넘어서는 값은 선언하지 않는다.

미검증(외부 문서 대조 전, 성공으로 위장하지 않음):
- `history_from`: Bitget 캔들 API의 실제 과거 데이터 보존 시작점은 공식
  문서로 확인하지 않았다. 임의 날짜를 채우면 §4.1 "조용한 채움 금지"
  정신에 반하므로 `None`(모름)으로 둔다.
- `rate_limit`: Bitget v2 spot public 엔드포인트의 초당 요청 한도는
  라이브 검증 전까지 보수적 추정치(10 req/s, burst 20)를 쓴다.
- `_MAX_CANDLES_PER_REQUEST`: 문서상 상한 미확인, 보수적으로 200개로 제한.

`list_instruments`/`subscribe`는 이 리프의 구현 대상이 아니다(task-1211
decision — "구현 대상 Protocol은 ... capabilities()... fetch_candles()...").
`list_instruments`이 반환할 `VenueListing.instrument_id`는 DC-2 심볼
마스터가 발급하는 ULID인데, 이 SPI 계층은 그 저장소(DC-5
`ports/instrument_repository.py`)에 접근하지 않으므로 여기서 임의로
지어내면 §4.1 불변조건(`instrument_id` 불변·유일)을 어길 위험이 있다.
`subscribe`(실시간 스트림 배선)는 DC-17(`realtime_fanout`) 선행 리프
몫이다. 둘 다 `NotImplementedError`로 fail-closed 한다 — 조용히 빈
결과를 돌려주면 "지원하지 않음"과 "아직 안 함"이 구분되지 않는다.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from decimal import Decimal

from src.data.models.base import AssetClass
from src.exchanges.bitget.adapter import BitgetAdapter
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

__all__ = ["BitgetProvider"]

_MAX_CANDLES_PER_REQUEST = 200  # 미검증(문서 미대조), 페이지네이션은 스콥 밖.

_CAPABILITIES = ProviderCapabilities(
    provider_id="bitget",
    asset_classes=frozenset({AssetClass.CRYPTO}),
    timeframes=frozenset(
        {
            Timeframe.M1,
            Timeframe.M5,
            Timeframe.M15,
            Timeframe.M30,
            Timeframe.H1,
            Timeframe.H4,
            Timeframe.D1,
        }
    ),
    history_from=None,  # 미검증(문서 미대조) — 임의 날짜로 채우지 않는다.
    realtime=True,  # BitgetAdapter.get_capabilities().supports_websocket
    delayed_seconds=0,
    max_symbols_per_request=1,  # REST candles 엔드포인트는 심볼 1개씩만 조회.
    rate_limit=RateLimitSpec(requests_per_second=Decimal(10), burst=20),  # 미검증
)


class BitgetProvider(BaseProviderAdapter):
    """`BitgetAdapter`(기존 `src/exchanges/bitget`)에 위임하는
    `MarketDataProvider`(DC-5) 구현체."""

    def __init__(self, adapter: BitgetAdapter, **kwargs: object) -> None:
        super().__init__(_CAPABILITIES, **kwargs)  # type: ignore[arg-type]
        self._adapter = adapter

    def capabilities(self) -> ProviderCapabilities:
        return _CAPABILITIES

    async def list_instruments(self, asset_class: AssetClass) -> list[VenueListing]:
        raise NotImplementedError(
            "BitgetProvider.list_instruments: DC-12 스콥 밖 — instrument_id(ULID) "
            "발급은 DC-2 심볼 마스터 소관이며 이 SPI 계층은 그 저장소를 참조하지 "
            "않는다(task-1211 decision)."
        )

    async def fetch_candles(
        self, listing: VenueListing, tf: Timeframe, span: TimeSpan
    ) -> CandleColumns:
        if listing.venue is not Venue.BITGET:
            raise ValueError(
                f"BitgetProvider는 Venue.BITGET listing만 처리한다: {listing.venue!r}"
            )
        symbol = to_canonical(Venue.BITGET, listing.venue_symbol)

        async def _op() -> CandleColumns:
            end_ms = str(int(span.end.timestamp() * 1000))
            raw_candles = await self._adapter.get_history_candles(
                symbol, tf.value, limit=_MAX_CANDLES_PER_REQUEST, end_time=end_ms
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
                        f"bitget: {symbol} {tf.value} 구간 [{span.start}, {span.end}) "
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
            "BitgetProvider.subscribe: DC-12 스콥 밖 — 실시간 스트림 배선은 "
            "DC-17(realtime_fanout) 선행 리프 몫이다(task-1211 decision)."
        )
        yield  # pragma: no cover — mypy가 AsyncIterator 반환형을 추론하도록 하는 도달 불가 표식
