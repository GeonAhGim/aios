"""DC-2 — instrument_id 발급·벤처 심볼 매핑·충돌 규칙(순수).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-2, §3.2(심볼 마스터), §4.2(생애주기 전이표), §9.2 DC-2.

`resolve()`: (venue, symbol) -> `InstrumentRef`(§3.2 `Instrument` + 매칭된
`VenueListing`). `register()`: 신규 instrument + 첫 listing. `change_symbol()`:
§3.2 심볼 변경(구 listing `delisted_at` 설정 + 새 listing, `instrument_id`
불변). §4.2 delisted→relisted "새 instrument 생성(구 id 유지 금지)" 가드는
`register()`가 강제 — 옛 delisted id 재사용은 `RelistingReuseError`.

순수(I/O·asyncpg 임포트 금지, L0-2). `instrument_id`는 호출자가 이미 발급해
넘긴다 — ULID는 발급 시각을 인코딩해 이 함수가 직접 생성하면 결정론이 깨진다.

기존 LA-7 `domain/reference/symbol_normalizer.py`를 재사용(재구현 아님) —
이 파일은 그 위의 대소문자 정규화와 등록 충돌·재상장 규칙만 더한다.

미검증: 구간 겹침 판정([listed_at, delisted_at) 반열린)은 §4.1 DB
EXCLUDE(gist) 제약을 순수 계층에서 재현한 사전 검사다 — 실제 강제는 DC-4의 몫.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.contracts.v2.instruments import (
    Instrument,
    InstrumentLifecycle,
    VenueListing,
)
from src.foundation.market_data.domain.reference.symbol_normalizer import (
    SymbolNormalizationError,
    to_canonical,
)

__all__ = [
    "InstrumentNotFoundError",
    "InstrumentRef",
    "RelistingReuseError",
    "SymbolConflictError",
    "SymbolMasterError",
    "change_symbol",
    "register",
    "resolve",
]


@dataclass(frozen=True)
class InstrumentRef:
    """`Instrument` + 매칭된 `VenueListing`의 합성 뷰(`resolve()` 전용, 비저장)."""

    instrument: Instrument
    listing: VenueListing


class SymbolMasterError(ValueError):
    """DC-2 공통 실패(fail-closed) — 절대 조용히 None/기본값을 반환하지 않는다."""


class InstrumentNotFoundError(SymbolMasterError):
    """`resolve()`가 매칭되는 (venue, symbol) 활성 `VenueListing`을 찾지 못함."""


class SymbolConflictError(SymbolMasterError):
    """§4.1 "venue_listings 기간 겹침 금지"를 새 등록/심볼변경이 위반."""


class RelistingReuseError(SymbolMasterError):
    """§4.2 delisted→relisted "새 instrument 생성(구 id 유지 금지)" 위반."""


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None:
        raise SymbolMasterError(f"{label}은 tz-aware datetime만 받는다")


def _normalize_symbol(venue: Venue, raw_symbol: str) -> str:
    """대소문자 정규화(대문자) 후 LA-7 형식 검증(재사용, 재구현 아님)."""
    candidate = raw_symbol.strip().upper()
    try:
        to_canonical(venue, candidate)
    except SymbolNormalizationError as exc:
        raise SymbolMasterError(f"venue_symbol 형식 오류: {raw_symbol!r}") from exc
    return candidate


def _is_active_at(listing: VenueListing, as_of: datetime) -> bool:
    if listing.listed_at > as_of:
        return False
    return listing.delisted_at is None or listing.delisted_at > as_of


def _overlaps(
    a_start: datetime, a_end: datetime | None, b_start: datetime, b_end: datetime | None
) -> bool:
    """반열린 구간 `[a_start, a_end)` vs `[b_start, b_end)` 겹침(`None`=+무한대)."""
    if a_end is not None and a_end <= b_start:
        return False
    if b_end is not None and b_end <= a_start:
        return False
    return True


def _check_no_overlap(
    listings: Sequence[VenueListing], venue: Venue, venue_symbol: str, opens_at: datetime
) -> None:
    """`opens_at`부터 여는 새 구간이 `listings`의 같은 (venue, venue_symbol) 구간과 겹치면 거부."""
    for listing in listings:
        if listing.venue is not venue or listing.venue_symbol != venue_symbol:
            continue
        if _overlaps(listing.listed_at, listing.delisted_at, opens_at, None):
            raise SymbolConflictError(
                f"겹치는 venue_symbol 구간: venue={venue.value} symbol={venue_symbol!r}"
            )


def _find_instrument(instruments: Sequence[Instrument], instrument_id: str) -> Instrument:
    for instrument in instruments:
        if instrument.instrument_id == instrument_id:
            return instrument
    raise InstrumentNotFoundError(f"listing은 있으나 instrument 레코드 없음: {instrument_id!r}")


def resolve(
    venue: Venue,
    symbol: str,
    *,
    instruments: Sequence[Instrument],
    listings: Sequence[VenueListing],
    as_of: datetime | None = None,
) -> InstrumentRef:
    """`(venue, symbol)` -> 활성 `InstrumentRef`(대소문자 정규화 매칭). `as_of` 없으면
    현재 활성만; 매칭 실패는 `None`이 아니라 `InstrumentNotFoundError`."""
    if as_of is not None:
        _require_aware(as_of, "as_of")
    normalized = _normalize_symbol(venue, symbol)
    for listing in listings:
        if listing.venue is not venue or listing.venue_symbol != normalized:
            continue
        if as_of is None:
            if listing.delisted_at is not None:
                continue
        elif not _is_active_at(listing, as_of):
            continue
        instrument = _find_instrument(instruments, listing.instrument_id)
        return InstrumentRef(instrument=instrument, listing=listing)
    raise InstrumentNotFoundError(f"매칭 instrument 없음: venue={venue.value} symbol={symbol!r}")


def register(
    *,
    instrument_id: str,
    venue: Venue,
    venue_symbol: str,
    asset_class: AssetClass,
    tick_size: Decimal,
    lot_size: Decimal,
    calendar_id: str,
    listed_at: datetime,
    created_at: datetime,
    base: str | None = None,
    quote: str | None = None,
    isin: str | None = None,
    figi: str | None = None,
    is_primary: bool = True,
    existing_instruments: Sequence[Instrument] = (),
    existing_listings: Sequence[VenueListing] = (),
) -> InstrumentRef:
    """신규 `Instrument`(상태 `PENDING`) + 첫 `VenueListing`. 거부(fail-closed): 기존
    비-DELISTED id 중복, 기존 DELISTED id 재사용(`RelistingReuseError`, 재상장 규칙),
    `(venue, venue_symbol)` 구간 겹침."""
    _require_aware(listed_at, "listed_at")
    _require_aware(created_at, "created_at")
    normalized = _normalize_symbol(venue, venue_symbol)
    for instrument in existing_instruments:
        if instrument.instrument_id != instrument_id:
            continue
        if instrument.lifecycle_state is InstrumentLifecycle.DELISTED:
            raise RelistingReuseError(
                f"delisted instrument_id 재사용 금지(재상장은 새 id 필요): {instrument_id!r}"
            )
        raise SymbolConflictError(f"이미 등록된 instrument_id: {instrument_id!r}")
    _check_no_overlap(existing_listings, venue, normalized, listed_at)
    instrument = Instrument(
        instrument_id=instrument_id,
        asset_class=asset_class,
        base=base,
        quote=quote,
        isin=isin,
        figi=figi,
        tick_size=tick_size,
        lot_size=lot_size,
        calendar_id=calendar_id,
        lifecycle_state=InstrumentLifecycle.PENDING,
        created_at=created_at,
    )
    listing = VenueListing(
        instrument_id=instrument_id,
        venue=venue,
        venue_symbol=normalized,
        listed_at=listed_at,
        delisted_at=None,
        is_primary=is_primary,
    )
    return InstrumentRef(instrument=instrument, listing=listing)


def change_symbol(
    *,
    current: VenueListing,
    new_venue_symbol: str,
    changed_at: datetime,
    existing_listings: Sequence[VenueListing] = (),
    is_primary: bool | None = None,
) -> tuple[VenueListing, VenueListing]:
    """§3.2 심볼 변경: 새 `VenueListing`(구 listing `delisted_at` 설정),
    `instrument_id` 불변. 반환은 `(closed_old, new)`."""
    _require_aware(changed_at, "changed_at")
    if current.delisted_at is not None:
        raise SymbolMasterError("이미 delisted된 listing은 심볼 변경 대상이 아니다")
    if changed_at <= current.listed_at:
        raise SymbolMasterError("changed_at은 listed_at 이후여야 한다")
    normalized = _normalize_symbol(current.venue, new_venue_symbol)
    key = (current.venue, current.venue_symbol, current.listed_at)
    others = [x for x in existing_listings if (x.venue, x.venue_symbol, x.listed_at) != key]
    _check_no_overlap(others, current.venue, normalized, changed_at)
    closed = current.model_copy(update={"delisted_at": changed_at})
    new_listing = VenueListing(
        instrument_id=current.instrument_id,
        venue=current.venue,
        venue_symbol=normalized,
        listed_at=changed_at,
        delisted_at=None,
        is_primary=current.is_primary if is_primary is None else is_primary,
    )
    return closed, new_listing
