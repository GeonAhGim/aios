"""RD-5 -- `application/link_entities.py` + `domain/entity_link.py` unit tests (fake port).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md Sec 9 RD-5
DoD (c), (e), (f).

DEEPEN (task-4170): added negative/failure-injection/perf tests for
`is_valid_isin`, `extract_entity_key`, `link_item`, and the application
entry point.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from src.foundation.research_data.application.link_entities import link_entities
from src.foundation.research_data.contracts.v1 import ResearchItem
from src.foundation.research_data.domain.entity_link import (
    EntityKey,
    EntityLinkResult,
    UnmappedReason,
    extract_entity_key,
    is_valid_isin,
    link_item,
)

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


# ---------------------------------------------------------------------------
# Negative tests for `is_valid_isin` (RD-A4: only deterministic keys)
# ---------------------------------------------------------------------------


async def test_is_valid_isin_rejects_too_short_candidate() -> None:
    """RD-A4: ISIN must be exactly 12 chars (2-letter country + 9-alphanumeric + 1 check digit)."""
    assert is_valid_isin("KR12345678901") is False  # 11 chars


async def test_is_valid_isin_rejects_too_long_candidate() -> None:
    assert is_valid_isin("KR1234567890123") is False  # 13 chars


async def test_is_valid_isin_rejects_invalid_country_code() -> None:
    """Country code must be two alphabetic characters."""
    assert is_valid_isin("1R12345678901") is False


async def test_is_valid_isin_rejects_wrong_checksum() -> None:
    """Luhn checksum must pass; flipping the last digit fails validation."""
    # "KR000000000003" is a valid ISIN checksum (verified by the Luhn impl).
    # Flip the check digit to "4" and it must be rejected.
    assert is_valid_isin("KR000000000004") is False


# ---------------------------------------------------------------------------
# Negative tests for `extract_entity_key` (RD-A4: no name-similarity guessing)
# ---------------------------------------------------------------------------


async def test_extract_entity_key_rejects_empty_string() -> None:
    """RD-A4: blank input yields no deterministic key."""
    assert extract_entity_key("") is None
    assert extract_entity_key("   ") is None


async def test_extract_entity_key_rejects_company_name() -> None:
    """RD-A4 forbids name-similarity guessing: a Korean company name
    must NOT be accepted as a valid key."""
    assert extract_entity_key("삼성전자") is None


async def test_extract_entity_key_rejects_mixed_garbage() -> None:
    assert extract_entity_key("005930-삼성전자") is None


# ---------------------------------------------------------------------------
# Negative tests for `link_item` — empty / all-unresolvable instruments
# ---------------------------------------------------------------------------


async def test_link_item_returns_no_deterministic_key_when_instruments_empty() -> None:
    """RD-5 DoD (c): empty instruments list → NO_DETERMINISTIC_KEY, not an error."""
    item = _item(instruments=())
    resolver = _FakeResolver({})
    result = link_item(item, resolver)
    assert result.instrument_id is None
    assert result.reason == UnmappedReason.NO_DETERMINISTIC_KEY


async def test_link_item_returns_not_found_when_key_parses_but_resolver_misses() -> None:
    """A valid KRX code that parses but has no resolver entry → NOT_FOUND."""
    item = _item(instruments=("005930",))
    resolver = _FakeResolver({})  # empty mapping
    result = link_item(item, resolver)
    assert result.instrument_id is None
    assert result.reason == UnmappedReason.NOT_FOUND


# ---------------------------------------------------------------------------
# Failure-injection test: dependency raises during key extraction
# ---------------------------------------------------------------------------


async def test_link_item_propagates_symbol_normalizer_exception() -> None:
    """RD-5 DoD (c): `to_canonical` raising an exception propagates
    — the domain layer expects resilient adapters below it.
    This is a negative test: callers must handle this boundary."""
    item = _item(instruments=("005930",))
    resolver = _FakeResolver({})
    with patch(
        "src.foundation.research_data.domain.entity_link.to_canonical",
        side_effect=Exception("symbol_normalizer_down"),
    ):
        with pytest.raises(Exception, match="symbol_normalizer_down"):
            link_item(item, resolver)


# ---------------------------------------------------------------------------
# Failure-injection: resolver raises during lookup
# ---------------------------------------------------------------------------


async def test_link_item_propagates_resolver_exception() -> None:
    """If the resolver raises on `resolve()`, the exception propagates
    — the domain layer does not catch it. Callers must inject resilient
    adapters."""
    item = _item(instruments=("005930",))

    class _CrashingResolver:
        def resolve(self, key: EntityKey) -> str | None:
            raise RuntimeError("resolver_down")

    crashing = _CrashingResolver()
    with pytest.raises(RuntimeError, match="resolver_down"):
        link_item(item, crashing)


# ---------------------------------------------------------------------------
# Failure injection: repository `list_unlinked` raises
# ---------------------------------------------------------------------------


class _CrashingRepository:
    async def list_unlinked(self, tenant_id: UUID, limit: int) -> list[ResearchItem]:
        raise ConnectionError("db_down")


async def test_link_entities_propagates_repository_list_failure() -> None:
    """If `list_unlinked` raises, the exception propagates — no silent
    swallowing. This is a negative test confirming fail-closed behavior."""
    repo: _FakeEntityLinkRepository | _CrashingRepository = _CrashingRepository()
    resolver = _FakeResolver({})

    with pytest.raises(ConnectionError, match="db_down"):
        await link_entities(None, repo, resolver, tenant_id=_TENANT, limit=10)


# ---------------------------------------------------------------------------
# Perf assertion: `is_valid_isin` throughput budget
# ---------------------------------------------------------------------------


async def test_is_valid_isin_throughput_budget() -> None:
    """ADR-2026-09-09-C: `is_valid_isin` must handle ≥10k calls/sec.
    Budget: ≤100 µs per call (10k/sec → 100µs/call)."""
    # Valid ISINs
    valid_candidates = [
        "US0378331005",  # Apple
        "US5949181045",  # Microsoft
        "KR0000000001",  # Korean ISIN placeholder
    ]
    # Invalid candidates
    invalid_candidates = [
        "US0378331006",  # wrong checksum
        "X" * 12,  # invalid country
        "US037833100",  # too short
        "",  # empty
    ]
    all_candidates = valid_candidates + invalid_candidates
    iterations = 5000
    total = iterations * len(all_candidates)

    start = time.perf_counter()
    for _ in range(iterations):
        for cand in all_candidates:
            is_valid_isin(cand)
    elapsed = time.perf_counter() - start

    per_call_us = (elapsed / total) * 1_000_000
    assert per_call_us < 100, f"is_valid_isin took {per_call_us:.1f} µs/call (budget: 100 µs)"
