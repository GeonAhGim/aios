"""DC-5 — `MarketDataProvider` SPI(벤더 중립 데이터 공급자 포트) + 에러 taxonomy.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-5, §3.1(SPI 계약 원문), §4.1(fail-closed), §9.2 DC-5.

§3.1 원문 시그니처를 그대로 옮긴다 — DC-11(`adapters/providers/base_adapter.py`)·
DC-12(거래소별 어댑터)가 이 Protocol에 1:1 의존하므로 메서드를 추가하거나
바꾸지 않는다(task-1126 decision). `ports/ingest_source.py`(LA-9)와 목적이
겹쳐 보여도 통합하지 않는다 — `IngestSource`는 거래소별 원시 캔들 페치만
다루고, 이 SPI는 provider 중립 신규 축(capabilities·entitlement·subscribe까지
포괄)이다.

반환은 전부 UTC tz-aware·`Decimal`이며 계보(`DataLineage`: provider_id·
fetched_at·raw_digest)를 남겨야 한다(§3.1). `fetch_candles`는 §3.1 원문대로
배치 단위 `CandleColumns`(ADR-2026-09-04-A)를 그대로 반환하므로, 배치 전체의
`DataLineage`는 이 반환값이 아니라 호출자가 저장 시점에 별도로 기록한다(LA-8
`domain/lineage.py`·LA-9 `BatchRepository`와 같은 자리). `subscribe()`는 이벤트
단위 스트림이라 이벤트마다 `DataLineage`를 실어 보낸다(`ProviderTick`/
`ProviderCandle`).

조용한 0 채움 금지(§4.1) — 커버리지 밖 구간은 `DataProviderError(
DATA_COVERAGE_MISSING)`으로, 권한 없는 피드는 `DATA_ENTITLEMENT_DENIED`로
예외를 던진다(빈 리스트로 대체하지 않는다).
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from enum import Enum
from typing import Literal, Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel

from src.core.exceptions import MihwaError
from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.domain.candle_columns import CandleColumns


class RateLimitSpec(BaseModel):
    """§3.1 `rate_limit`. 토큰버킷 파라미터 선언값만 담는다 — 실제 제한
    적용은 DC-11 `base_adapter.py` 소관."""

    requests_per_second: Decimal
    burst: int


class ProviderCapabilities(BaseModel):
    """§3.1 원문 그대로. 필드 순서·이름 변경 금지(DC-11/12 의존)."""

    provider_id: str
    asset_classes: frozenset[AssetClass]
    timeframes: frozenset[Timeframe]
    history_from: AwareDatetime | None
    realtime: bool
    delayed_seconds: int
    max_symbols_per_request: int
    rate_limit: RateLimitSpec


class TimeSpan(BaseModel):
    """`fetch_candles`의 조회 구간 `[start, end)`. §3.1이 이름만 언급하고
    본문 정의는 없어 LA-9 `IngestSource.fetch_candles`의 `[start, end)`
    규약을 그대로 따른다."""

    start: AwareDatetime
    end: AwareDatetime


class DataLineage(BaseModel):
    """§3.1 "lineage(provider_id, fetched_at, raw_digest) 필수"."""

    provider_id: str
    fetched_at: AwareDatetime
    raw_digest: str


class ProviderTick(BaseModel):
    listing: VenueListing
    price: Decimal
    quantity: Decimal
    side: Literal["buy", "sell"]
    traded_at: AwareDatetime
    lineage: DataLineage


class ProviderCandle(BaseModel):
    listing: VenueListing
    tf: Timeframe
    open_time: AwareDatetime
    close_time: AwareDatetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    lineage: DataLineage


TickOrCandle = ProviderTick | ProviderCandle


@runtime_checkable
class MarketDataProvider(Protocol):
    """§3.1 원문. `domain/`·`application/`은 이 Protocol만 알고 실제 벤더
    구현(DC-12)은 모른다(71번 §4)."""

    def capabilities(self) -> ProviderCapabilities: ...

    async def list_instruments(self, asset_class: AssetClass) -> list[VenueListing]:
        """이 공급자가 다루는 벤처 심볼 목록. 자산군 미지원이면 빈 리스트
        (오류 아님) — 조회 자체의 실패는 예외로 던진다."""
        ...

    async def fetch_candles(
        self, listing: VenueListing, tf: Timeframe, span: TimeSpan
    ) -> CandleColumns:
        """`[span.start, span.end)`. 공급자가 그 구간을 커버하지 못하면
        `DataProviderError(DATA_COVERAGE_MISSING)`(§4.1, 0/NaN 채움 금지)."""
        ...

    async def subscribe(
        self, listings: Sequence[VenueListing]
    ) -> AsyncIterator[TickOrCandle]:
        """실시간/지연 스트림. `capabilities().realtime=False`면 지연 피드
        (`delayed_seconds`)만 보낸다."""
        ...


class DataProviderErrorCode(str, Enum):
    """§3.1 에러 taxonomy. 이 4개 밖의 실패는 호출부가 원본 예외를 그대로
    전파해야 한다 — 미지의 실패를 임의로 이 목록에 끼워 맞추지 않는다
    (§4.1 fail-closed와 같은 원칙, `ExchangeErrorKind.UNKNOWN_RESPONSE`와
    대비되는 지점: 여기는 "모르면 이 코드로 뭉갠다"가 아니라 "모르면 이
    taxonomy를 쓰지 않는다")."""

    DATA_PROVIDER_RATE_LIMITED = "DATA_PROVIDER_RATE_LIMITED"
    DATA_PROVIDER_UNAVAILABLE = "DATA_PROVIDER_UNAVAILABLE"
    DATA_ENTITLEMENT_DENIED = "DATA_ENTITLEMENT_DENIED"
    DATA_COVERAGE_MISSING = "DATA_COVERAGE_MISSING"


_RETRYABLE_CODES = frozenset(
    {
        DataProviderErrorCode.DATA_PROVIDER_RATE_LIMITED,
        DataProviderErrorCode.DATA_PROVIDER_UNAVAILABLE,
    }
)


class DataProviderError(MihwaError):
    """§3.1 에러 4종의 공통 표현. `retryable`은 `code`로 결정되고 호출부가
    덮어쓰지 않는다(재시도 정책 자체는 DC-11 `base_adapter.py` 소관, 여기는
    분류만 한다)."""

    def __init__(
        self,
        code: DataProviderErrorCode,
        *,
        provider_id: str,
        retry_after_sec: float | None = None,
        message: str | None = None,
    ) -> None:
        self.code = code
        self.retryable = code in _RETRYABLE_CODES
        self.provider_id = provider_id
        self.retry_after_sec = retry_after_sec
        super().__init__(
            message or f"데이터 공급 오류: code={code.value} provider={provider_id}"
        )
