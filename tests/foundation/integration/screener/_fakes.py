"""In-memory fakes for screener application-layer tests (no DB).

Shared by `__init__.py` (negative/failure-injection/perf) and
`test_application_fakes_integration.py` (cross-module integration) — split
out per CLAUDE.md §9 file policy (fakes/helpers are a distinct
responsibility from the test cases that consume them).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import InstrumentRef, SymbolStatus, Venue
from src.foundation.screener.contracts.v1 import (
    IndicatorFilter,
    SavedScreenerView,
    ScreenAlertOperator,
    ScreenAlertView,
    ScreenDefinition,
    SharedScreenerView,
)
from src.foundation.screener.ports.repository import ScreenAlertLimitError

_LISTED_AT = datetime(2020, 1, 1, tzinfo=timezone.utc)


def instrument(instrument_id: UUID, *, venue: Venue = Venue.KIS_KRX, symbol: str) -> InstrumentRef:
    return InstrumentRef(
        instrument_id=instrument_id,
        venue=venue,
        canonical_symbol=symbol,
        venue_symbol=symbol,
        asset_class=AssetClass.KR_EQUITY,
        base=None,
        quote=None,
        tick_size=Decimal("1"),
        lot_size=Decimal("1"),
        status=SymbolStatus.LISTED,
        listed_at=_LISTED_AT,
        delisted_at=None,
    )


def definition(*, condition: str = "close > 100", universe: str = "KIS_KRX") -> ScreenDefinition:
    return ScreenDefinition(universe=universe, filters=(IndicatorFilter(condition=condition),))


class FakeSavedScreenerRepository:
    """`SavedScreenerRepository` 완전 인메모리 구현 — 저장 시도 횟수를 세어
    save_screen이 컴파일 실패 시 저장소를 아예 건드리지 않는 것을 증명한다."""

    def __init__(self, *, raise_on_save: Exception | None = None) -> None:
        self._rows: dict[tuple[UUID, UUID], SavedScreenerView] = {}
        self._raise_on_save = raise_on_save
        self.save_calls = 0

    async def save(
        self, *, tenant_id: UUID, name: str, definition: ScreenDefinition
    ) -> SavedScreenerView:
        self.save_calls += 1
        if self._raise_on_save is not None:
            raise self._raise_on_save
        now = datetime.now(timezone.utc)
        view = SavedScreenerView(
            id=uuid4(),
            tenant_id=tenant_id,
            name=name,
            definition=definition,
            created_at=now,
            updated_at=now,
        )
        self._rows[(tenant_id, view.id)] = view
        return view

    async def list_for_tenant(self, tenant_id: UUID) -> tuple[SavedScreenerView, ...]:
        return tuple(v for (t, _), v in self._rows.items() if t == tenant_id)

    async def get(self, tenant_id: UUID, screener_id: UUID) -> SavedScreenerView | None:
        return self._rows.get((tenant_id, screener_id))

    async def delete(self, tenant_id: UUID, screener_id: UUID) -> bool:
        return self._rows.pop((tenant_id, screener_id), None) is not None


class FakeSharedScreenerRepository:
    """MP-3 immutable-version 규칙을 인메모리로 흉내 — 매 호출마다 새 version을
    append하고 과거 행은 절대 갱신하지 않는다."""

    def __init__(self) -> None:
        self._versions: dict[UUID, list[SharedScreenerView]] = {}
        self.create_version_calls = 0

    async def create_version(
        self, *, tenant_id: UUID, screener_id: UUID, name: str, definition: ScreenDefinition
    ) -> SharedScreenerView:
        self.create_version_calls += 1
        existing = self._versions.setdefault(screener_id, [])
        next_version = (existing[-1].version + 1) if existing else 1
        view = SharedScreenerView(
            id=uuid4(),
            screener_id=screener_id,
            tenant_id=tenant_id,
            name=name,
            definition=definition,
            version=next_version,
            created_at=datetime.now(timezone.utc),
        )
        existing.append(view)
        return view

    async def get_latest(self, screener_id: UUID) -> SharedScreenerView | None:
        versions = self._versions.get(screener_id)
        return versions[-1] if versions else None

    async def list_versions(self, screener_id: UUID) -> tuple[SharedScreenerView, ...]:
        return tuple(self._versions.get(screener_id, ()))


class FakeScreenAlertRepository:
    """`ScreenAlertRepository` 인메모리 구현 — `limit`이 주어지면
    `MAX_ACTIVE_SCREEN_ALERTS_PER_TENANT` 캡 위반을 `ScreenAlertLimitError`로 재현한다."""

    def __init__(self, *, limit: int | None = None) -> None:
        self._rows: dict[UUID, ScreenAlertView] = {}
        self._limit = limit
        self.create_calls = 0

    async def create(
        self, *, tenant_id: UUID, screener_id: UUID, operator: str, threshold: int
    ) -> ScreenAlertView:
        self.create_calls += 1
        if self._limit is not None:
            active = sum(
                1 for v in self._rows.values() if v.tenant_id == tenant_id and v.status == "ACTIVE"
            )
            if active >= self._limit:
                raise ScreenAlertLimitError(f"tenant {tenant_id} at cap {self._limit}")
        view = ScreenAlertView(
            id=uuid4(),
            tenant_id=tenant_id,
            screener_id=screener_id,
            operator=cast(ScreenAlertOperator, operator),
            threshold=threshold,
            status="ACTIVE",
            created_at=datetime.now(timezone.utc),
        )
        self._rows[view.id] = view
        return view

    async def list_for_tenant(self, tenant_id: UUID) -> tuple[ScreenAlertView, ...]:
        return tuple(v for v in self._rows.values() if v.tenant_id == tenant_id)

    async def get(self, tenant_id: UUID, alert_id: UUID) -> ScreenAlertView | None:
        row = self._rows.get(alert_id)
        if row is None or row.tenant_id != tenant_id:
            return None
        return row

    async def cancel(self, tenant_id: UUID, alert_id: UUID) -> bool:
        row = self._rows.get(alert_id)
        if row is None or row.tenant_id != tenant_id or row.status != "ACTIVE":
            return False
        self._rows[alert_id] = row.model_copy(update={"status": "CANCELLED"})
        return True

    async def mark_triggered(self, alert_id: UUID, *, matched_count: int) -> ScreenAlertView | None:
        row = self._rows.get(alert_id)
        if row is None or row.status != "ACTIVE":
            return None
        updated = row.model_copy(
            update={
                "status": "TRIGGERED",
                "triggered_at": datetime.now(timezone.utc),
                "triggered_count": matched_count,
            }
        )
        self._rows[alert_id] = updated
        return updated

    async def list_active(self) -> tuple[ScreenAlertView, ...]:
        return tuple(v for v in self._rows.values() if v.status == "ACTIVE")


class FakeFieldSource:
    """`ScreenerFieldSource` 인메모리 구현 — `raise_on_read`가 주어지면
    `read_fields` 호출 시 그 예외를 던져 의존성 장애를 흉내낸다."""

    def __init__(
        self,
        instruments: Sequence[InstrumentRef],
        fields: Mapping[UUID, Mapping[str, Decimal]],
        *,
        raise_on_read: Exception | None = None,
    ) -> None:
        self._instruments = list(instruments)
        self._fields = fields
        self._raise_on_read = raise_on_read

    async def universe_page(
        self, *, venues: frozenset[Venue], after: UUID | None, limit: int
    ) -> list[InstrumentRef]:
        pool = [i for i in self._instruments if i.venue in venues]
        if after is not None:
            idx = next((k for k, i in enumerate(pool) if i.instrument_id == after), len(pool))
            pool = pool[idx + 1 :]
        return pool[:limit]

    async def read_fields(
        self,
        *,
        instrument_ids_by_venue: Mapping[Venue, Sequence[UUID]],
        field_names: frozenset[str],
        as_of: datetime,
    ) -> dict[UUID, dict[str, Decimal]]:
        if self._raise_on_read is not None:
            raise self._raise_on_read
        result: dict[UUID, dict[str, Decimal]] = {}
        for ids in instrument_ids_by_venue.values():
            for instrument_id in ids:
                row = self._fields.get(instrument_id)
                if row is None:
                    continue
                result[instrument_id] = {k: v for k, v in row.items() if k in field_names}
        return result
