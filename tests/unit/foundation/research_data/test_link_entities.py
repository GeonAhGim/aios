"""RD-5 -- `application/link_entities.py` unit tests (fake port).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md Sec 9 RD-5
DoD (c), (e), (f).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from src.foundation.research_data.application.link_entities import link_entities
from src.foundation.research_data.contracts.v1 import ResearchItem
from src.foundation.research_data.domain.entity_link import EntityKey, EntityLinkResult

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_TENANT = uuid4()


def _item(*, instruments: tuple[str, ...] = ()) -> ResearchItem:
    return ResearchItem(
        item_id=uuid4(),
        source_id="OPENDART",
        kind="filing",
        published_at=_NOW,
        known_at=_NOW,
        instruments=instruments,
        title="title",
        body_ref=None,
        url="https://example.invalid/x",
        language="ko",
        hash="deadbeef",
        revision_of=None,
    )


class _FakeResolver:
    """Deterministic-key-value -> instrument_id map (RD-5 does not need
    symbol_master data here; that delegation is covered in
    test_entity_link.py)."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    def resolve(self, key: EntityKey) -> str | None:
        return self._mapping.get(key.value)


class _FakeEntityLinkRepository:
    """In-memory fake -- `list_unlinked` stops returning an item as soon as
    `save_links` has recorded a successful match for it (mirrors the real
    Postgres query `WHERE instrument_id IS NULL`)."""

    def __init__(self, items: list[ResearchItem]) -> None:
        self._pool: dict[UUID, ResearchItem] = {item.item_id: item for item in items}
        self.save_calls: list[list[EntityLinkResult]] = []

    async def list_unlinked(
        self, conn: object, *, tenant_id: UUID, limit: int
    ) -> list[ResearchItem]:
        return list(self._pool.values())[:limit]

    async def save_links(self, conn: object, results: list[EntityLinkResult]) -> None:
        self.save_calls.append(list(results))
        for result in results:
            if result.instrument_id is not None:
                self._pool.pop(result.item_id, None)


async def test_link_entities_batch_leaves_unmapped_items_unmapped_without_exception() -> None:
    mapped_item = _item(instruments=("005930",))
    unmapped_item_a = _item(instruments=("삼성전자우 유상증자",))
    unmapped_item_b = _item(instruments=())
    repo = _FakeEntityLinkRepository([mapped_item, unmapped_item_a, unmapped_item_b])
    resolver = _FakeResolver({"005930": "INSTR-1"})

    results = await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=10)

    assert len(results) == 3
    mapped = [r for r in results if r.instrument_id is not None]
    unmapped = [r for r in results if r.instrument_id is None]
    assert len(mapped) == 1
    assert mapped[0].item_id == mapped_item.item_id
    assert len(unmapped) == 2
    assert repo.save_calls == [results]


async def test_link_entities_is_idempotent_on_the_second_run() -> None:
    """RD-5 DoD (e): once every item resolves, the second run's `save_links`
    call receives an empty list -- `list_unlinked` no longer returns them."""
    item_a = _item(instruments=("005930",))
    item_b = _item(instruments=("000660",))
    repo = _FakeEntityLinkRepository([item_a, item_b])
    resolver = _FakeResolver({"005930": "INSTR-1", "000660": "INSTR-2"})

    first_results = await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=10)
    second_results = await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=10)

    assert len(first_results) == 2
    assert all(r.instrument_id is not None for r in first_results)
    assert second_results == []
    assert repo.save_calls[0] == first_results
    assert repo.save_calls[1] == []
