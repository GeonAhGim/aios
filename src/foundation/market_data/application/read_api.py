"""LA-24 — HTTP 읽기 API가 쓰는 식별자 해석·이용권 판정·페이지 분할(순수 + 포트 호출).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-24.

`src/api/routers/market_data.py`가 300줄 상한(P6.line_cap)에 닿아 전송
계층과 무관한 부분을 여기로 분리했다. SQL·HTTP 모두 없다 — 포트
(`ReferenceRepository`/`ReferenceReadRepository`/`EntitlementPort`/
`VenueRegistrySource`)만 호출한다. 예외 3종은 `exception_registry_foundation.py`
가 상태코드로 번역한다(404/409/400).

식별자 규칙(스펙 "둘 다 허용, 응답에 둘 다 표기"): `instrument_id`(md_instrument
UUID)가 있으면 우선하고 `venue`는 일치 검증만 한다; 없으면 `symbol`(벤처
심볼)을 `md_symbol_alias` 유효기간으로 해석한다. 미등록·벤처 불일치·이용권
거부는 **전부 같은** `MarketDataNotFoundError`다(타 테넌트 404 동형).

DC-28(ADR-2026-09-06-H D2) — `authorize_redistribution()`은 이 파일이 이미
쓰는 "이용권 거부는 존재 여부와 구분되지 않는다" 원칙을 재배포 스코프에도
그대로 적용한다: 소스 계약이 없거나(D2 "미지정은 NONE으로 취급") 이 용도를
허용하지 않으면 같은 `MarketDataNotFoundError`로 접는다. 순수 판정
(`permits_use`)은 `domain/entitlement/source_contract.py`(DC-27) 소관이고,
포트 호출(`authorize_source_access`)도 재구현하지 않는다 — 이 함수는 둘을
조합해 강제 지점(읽기 API·차트·백테스트·내보내기)이 공유하는 게이트 하나만
제공한다.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

import asyncpg

from src.foundation.market_data.application.authorize_source_access import (
    authorize_source_access,
)
from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    InstrumentRef,
    Timeframe,
    Venue,
)
from src.foundation.market_data.domain.entitlement.policy import (
    Entitlement,
    EntitlementSubject,
    FeedRequest,
)
from src.foundation.market_data.domain.entitlement.source_contract import DataUse, permits_use
from src.foundation.market_data.ports.entitlement import EntitlementPort, VenueRegistrySource
from src.foundation.market_data.ports.reference_repository import (
    ReferenceReadRepository,
    ReferenceRepository,
)
from src.foundation.market_data.ports.source_contract_repository import SourceContractRepository

__all__ = [
    "DataCoverageMissingError",
    "MarketDataNotFoundError",
    "MarketDataQueryError",
    "authorize_feed",
    "authorize_redistribution",
    "authorize_venue",
    "paginate_candles",
    "resolve_instrument",
    "validate_span",
]

_NOT_FOUND_MESSAGE = "인스트루먼트를 찾을 수 없습니다."


class MarketDataNotFoundError(Exception):
    """미등록 심볼/instrument_id, 벤처 불일치, 이용권 거부(타 테넌트)를 모두
    같은 404로 접는다 — 존재 여부를 상태코드로 누설하지 않는다."""


class DataCoverageMissingError(Exception):
    """요청 구간이 저장 커버리지 밖(기대 세션은 있는데 저장 캔들 0건).
    §4.1 0/NaN 채움 금지 → 409 `DATA_COVERAGE_MISSING`."""


class MarketDataQueryError(Exception):
    """쿼리 파라미터 조합 오류(식별자 없음, naive datetime, start ≥ end) — 400."""


def _require_aware(name: str, value: datetime | None) -> None:
    if value is not None and value.tzinfo is None:
        raise MarketDataQueryError(f"{name}은(는) tz-aware datetime(UTC)이어야 합니다.")


def validate_span(start: datetime, end: datetime, *extra: tuple[str, datetime | None]) -> None:
    _require_aware("start", start)
    _require_aware("end", end)
    for name, value in extra:
        _require_aware(name, value)
    if start >= end:
        raise MarketDataQueryError("start는 end보다 앞서야 합니다.")


def paginate_candles(
    candles: list[CandleRecord], cursor: datetime | None, limit: int
) -> tuple[list[CandleRecord], str | None]:
    """순수 — `open_time >= cursor`인 첫 캔들부터 `limit`개. 다음 커서는 그
    다음 캔들의 `open_time`(ISO 8601), 더 없으면 None. 시계열 전체(요청 구간)를
    한 번 읽어 자르므로 `as_of` 스냅샷 결정론이 페이지 간에도 유지된다."""
    first = 0
    if cursor is not None:
        first = next((i for i, c in enumerate(candles) if c.open_time >= cursor), len(candles))
    page = candles[first : first + limit]
    after = first + limit
    if after >= len(candles):
        return page, None
    # 응답 본문의 datetime 직렬화(pydantic, `Z`)와 같은 표기로 맞춘다.
    return page, candles[after].open_time.isoformat().replace("+00:00", "Z")


async def resolve_instrument(
    conn: asyncpg.Connection,
    *,
    refs: ReferenceRepository,
    reader: ReferenceReadRepository,
    venue: Venue | None,
    symbol: str | None,
    instrument_id: UUID | None,
    now: datetime,
) -> InstrumentRef:
    if instrument_id is None and symbol is None:
        raise MarketDataQueryError("symbol 또는 instrument_id 중 하나는 필요합니다.")
    if instrument_id is not None:
        by_id = await reader.get_by_id(conn, instrument_id)
        if by_id is None or (venue is not None and by_id.venue is not venue):
            raise MarketDataNotFoundError(_NOT_FOUND_MESSAGE)
        return by_id
    if venue is None or symbol is None:
        raise MarketDataQueryError("symbol로 조회하려면 venue가 필요합니다.")
    by_symbol = await refs.get_instrument(conn, venue, symbol, now)
    if by_symbol is None:
        raise MarketDataNotFoundError(_NOT_FOUND_MESSAGE)
    return by_symbol


async def authorize_feed(
    port: EntitlementPort, *, tenant_id: UUID, subject_id: UUID, inst: InstrumentRef,
    timeframe: Timeframe,
) -> Entitlement:
    """캔들 피드는 (venue, asset_class, instrument, timeframe) 축으로 포트에 묻는다."""
    subject = EntitlementSubject(tenant_id=tenant_id, subject_id=subject_id, grants=())
    feed = FeedRequest(
        venue=inst.venue,
        asset_class=inst.asset_class,
        instrument_id=str(inst.instrument_id),
        timeframe=timeframe,
        want_realtime=False,
    )
    decision = await port.allowed(subject, feed)
    if not decision.allowed:
        raise MarketDataNotFoundError(_NOT_FOUND_MESSAGE)
    return decision


async def authorize_venue(
    source: VenueRegistrySource, *, tenant_id: UUID, inst: InstrumentRef
) -> None:
    """참조데이터(목록·별칭)는 timeframe 축이 없어 벤처 단위 등록으로 판정한다."""
    if inst.venue not in await source.registered_venues(tenant_id):
        raise MarketDataNotFoundError(_NOT_FOUND_MESSAGE)


async def authorize_redistribution(
    conn: asyncpg.Connection,
    source_id: str,
    *,
    repo: SourceContractRepository,
    clock: Callable[[], datetime],
    use: DataUse,
) -> None:
    """D2 강제 지점(읽기 API·차트·백테스트·내보내기) 공통 게이트.

    `source_id`는 벤처별 소스 계약 행을 가리킨다(market_data 벤더는
    `Venue.value`가 곧 `source_contract.source_id`다 — 별도 매핑 테이블 없이
    어댑터가 이미 아는 문자열을 그대로 쓴다). 계약이 없거나 만료됐거나
    (`authorize_source_access`) 있어도 이 `use`를 허용하지 않으면(`permits_use`)
    전부 같은 `MarketDataNotFoundError`다 — 어느 사유인지 상태코드로 구분하면
    "이 소스는 계약이 있다/없다"·"이 등급으로는 안 된다"를 응답만으로 알 수
    있게 돼 authorize_feed와 같은 존재-누설 문제가 생긴다."""
    grant = await authorize_source_access(conn, source_id, repo=repo, clock=clock)
    if not grant.allowed or grant.redistribution_scope is None:
        raise MarketDataNotFoundError(_NOT_FOUND_MESSAGE)
    if not permits_use(grant.redistribution_scope, use):
        raise MarketDataNotFoundError(_NOT_FOUND_MESSAGE)
