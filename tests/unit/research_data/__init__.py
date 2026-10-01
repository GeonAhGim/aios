"""RD-2/3/5/7 -- negative/failure-injection coverage (DEEPEN task-10176).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §2.1
domain/entity_link.py (RD-5), domain/revision.py (RD-3),
domain/known_at.py (RD-2), domain/redistribution.py (RD-3),
application/query.py (RD-7); §4 RD-A1/A2/A4.

DoD (D2 minimum):
  - negative tests >= 3 (deterministic-key rejection, revision chain
    cycle/branch errors, point-in-time violation, naive as_of rejection,
    redistribution denial)
  - failure injection: entity resolver crash propagates (fail-closed,
    never silently swallowed)
  - performance assertion: search() stays under budget for 10k items
  - gate-red reproduction: RD-A1 invariant holds through the search
    pipeline (no future-known_at item is ever returned)

Invariant references (docs/design/INVARIANTS.md):
  RD-A1: an item with known_at > as_of is never returned by any read path.
  RD-A2: a correction is a new row linked by revision_of, never an in-place
    edit -- a chain must stay single-strand with no cycle.
  RD-A4: no name-similarity guessing -- only deterministic key shapes map
    to an instrument_id.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from src.foundation.market_data.domain.entitlement.source_contract import (
    RedistributionScope,
    SourceCapability,
    SourceContractDenialReason,
    SourceContractGrant,
    SourceContractTier,
)
from src.foundation.research_data.application.query import search
from src.foundation.research_data.contracts.v1 import (
    ResearchItem,
    ResearchItemKind,
    SourceMeta,
)
from src.foundation.research_data.domain.entity_link import (
    UnmappedReason,
    is_valid_isin,
    link_item,
)
from src.foundation.research_data.domain.known_at import (
    PointInTimeViolationError,
    assert_point_in_time,
)
from src.foundation.research_data.domain.redistribution import (
    RedistributionViolationError,
    assert_redistribution_allowed,
)
from src.foundation.research_data.domain.revision import (
    RevisionBranchError,
    RevisionCycleError,
    link_revision_chain,
)

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

_BASE_TIME = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def _item(
    *,
    item_id: UUID | None = None,
    known_at: datetime | None = None,
    instruments: tuple[str, ...] | None = None,
    kind: ResearchItemKind = "filing",
    revision_of: UUID | None = None,
    source_id: str = "test-source",
    title: str = "test title",
    body_ref: str | None = "/test/ref",
    url: str = "https://example.com/test",
    language: str = "ko",
    hash: str = "abc123",
    published_at: datetime | None = None,
) -> ResearchItem:
    return ResearchItem(
        item_id=item_id or uuid4(),
        source_id=source_id,
        kind=kind,
        published_at=published_at or known_at or _BASE_TIME,
        known_at=known_at or _BASE_TIME,
        instruments=instruments if instruments is not None else ("005930",),
        title=title,
        body_ref=body_ref,
        url=url,
        language=language,
        hash=hash,
        revision_of=revision_of,
    )


class _FakeResolver:
    """EntityResolver that returns None for all keys (not-found)."""

    def resolve(self, key: object) -> str | None:
        return None


_FAKE_RESOLVER = _FakeResolver()


# ---------------------------------------------------------------------------
# negative tests (>= 3 required)
# ---------------------------------------------------------------------------


def test_invalid_isin_checksum_rejected() -> None:
    """RD-5: ISIN with a bad Luhn checksum is rejected."""
    assert is_valid_isin("KR0001241009") is False
    assert is_valid_isin("KR0001241001") is False


def test_valid_isin_checksum_accepted() -> None:
    """RD-5: ISIN with a correct Luhn checksum is accepted."""
    assert is_valid_isin("US0378331005") is True


def test_isin_wrong_length_rejected() -> None:
    """RD-5: ISIN shorter or longer than 12 characters is rejected."""
    assert is_valid_isin("KR000124100") is False
    assert is_valid_isin("KR00012410001") is False


def test_isin_non_alpha_country_code_rejected() -> None:
    """RD-5: ISIN must start with two alpha characters (country code)."""
    assert is_valid_isin("1R0001241000") is False
    assert is_valid_isin("K10001241000") is False


def test_empty_instruments_yields_no_deterministic_key() -> None:
    """RD-A4: a ResearchItem with no instruments entries yields
    NO_DETERMINISTIC_KEY, never a guess."""
    item = _item(instruments=())
    result = link_item(item, _FAKE_RESOLVER)
    assert result.instrument_id is None
    assert result.reason is UnmappedReason.NO_DETERMINISTIC_KEY


def test_non_matching_instrument_yields_not_found() -> None:
    """RD-5: a key that parses but does not resolve yields NOT_FOUND, not
    an exception."""
    item = _item(instruments=("005930",))
    result = link_item(item, _FAKE_RESOLVER)
    assert result.instrument_id is None
    assert result.reason is UnmappedReason.NOT_FOUND


def test_revision_chain_missing_parent_raises_cycle_error() -> None:
    """RD-A2: revision_of pointing outside the given item set never
    resolves to a single root -- rejected as a cycle, not silently
    dropped."""
    child = _item(revision_of=uuid4())
    with pytest.raises(RevisionCycleError):
        link_revision_chain([child])


def test_revision_chain_self_reference_raises_cycle_error() -> None:
    """RD-A2: an item cannot be its own revision target."""
    self_id = uuid4()
    item = _item(item_id=self_id, revision_of=self_id)
    with pytest.raises(RevisionCycleError):
        link_revision_chain([item])


def test_revision_chain_branch_raises_branch_error() -> None:
    """RD-A2: two items both claiming revision_of the same parent breaks
    the single-strand invariant."""
    parent = _item()
    child_a = _item(revision_of=parent.item_id)
    child_b = _item(revision_of=parent.item_id)
    with pytest.raises(RevisionBranchError):
        link_revision_chain([parent, child_a, child_b])


def test_future_known_at_rejected_by_point_in_time_check() -> None:
    """RD-A1: an item with known_at in the future relative to as_of is
    rejected, not clamped or silently included."""
    future_item = _item(known_at=datetime(2027, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(PointInTimeViolationError):
        assert_point_in_time(future_item, _BASE_TIME)


def test_search_rejects_naive_as_of() -> None:
    """RD-A1: a naive (tz-less) as_of is rejected outright -- no implicit
    UTC assumption."""
    with pytest.raises(ValueError, match="tz-aware"):
        search([_item()], as_of=datetime(2026, 6, 15, 12, 0, 0))


def test_redistribution_denied_for_link_only_source_with_body() -> None:
    """RD-3: a link_only source's item may never carry a stored body_ref."""
    source = SourceMeta(
        source_id="s1",
        publisher="pub",
        redistribution="link_only",
        license_ref="ref",
        rate_limit=10,
        coverage="cov",
    )
    item = _item(source_id="s1", body_ref="/body/ref")
    grant = SourceContractGrant(
        allowed=True,
        tier=SourceContractTier.FREE,
        redistribution_scope=RedistributionScope.DISPLAY,
        rate_limit=10,
        quota=100,
        capability=SourceCapability(
            asset_classes=frozenset({"EQUITY_KR"}),
            resolutions=frozenset(),
            corporate_actions=True,
        ),
        denial_reason=None,
    )
    with pytest.raises(RedistributionViolationError):
        assert_redistribution_allowed(item, source, grant)


def test_redistribution_denied_when_contract_grant_denied() -> None:
    """RD-3: the DC-27 tier gate denial (e.g. contract NOT_FOUND) blocks
    storage even when the license itself would have allowed it."""
    source = SourceMeta(
        source_id="s1",
        publisher="pub",
        redistribution="store_full",
        license_ref="ref",
        rate_limit=10,
        coverage="cov",
    )
    item = _item(source_id="s1", body_ref=None)
    grant = SourceContractGrant(
        allowed=False,
        tier=None,
        redistribution_scope=None,
        rate_limit=None,
        quota=None,
        capability=None,
        denial_reason=SourceContractDenialReason.NOT_FOUND,
    )
    with pytest.raises(RedistributionViolationError):
        assert_redistribution_allowed(item, source, grant)


# ---------------------------------------------------------------------------
# failure injection
# ---------------------------------------------------------------------------


def test_entity_resolver_crash_propagates_fail_closed() -> None:
    """Failure injection: a resolver that raises must not be swallowed into
    a false NOT_FOUND -- link_item has no try/except around the resolver
    call, so the caller sees the real failure and can retry/alert instead
    of silently mis-filing the item as unmapped."""

    class _CrashingResolver:
        def resolve(self, key: object) -> str | None:
            raise ConnectionError("resolver down")

    item = _item(instruments=("005930",))
    with pytest.raises(ConnectionError, match="resolver down"):
        link_item(item, _CrashingResolver())


def test_revision_chain_break_injection() -> None:
    """Failure injection: a valid 3-item chain is accepted, but removing
    the middle revision breaks the single-root traversal and must raise,
    not silently return a partial ordering."""
    grandparent = _item()
    parent = _item(revision_of=grandparent.item_id)
    child = _item(revision_of=parent.item_id)

    ordered = link_revision_chain([grandparent, parent, child])
    assert ordered == (grandparent, parent, child)

    with pytest.raises(RevisionCycleError):
        link_revision_chain([grandparent, child])


# ---------------------------------------------------------------------------
# performance assertion
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_search_latency_under_budget_for_10k_items() -> None:
    """Performance: search() over 10k items completes well under a 300ms
    budget (RD-7)."""
    import time

    items = [
        _item(
            known_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            instruments=("005930",),
        )
        for _ in range(10_000)
    ]

    start = time.perf_counter()
    result = search(items, as_of=datetime(2026, 6, 15, tzinfo=timezone.utc))
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert len(result) == 10_000
    assert elapsed_ms < 300, f"search(10k) took {elapsed_ms:.1f}ms > 300ms budget"


# ---------------------------------------------------------------------------
# gate-red reproduction
# ---------------------------------------------------------------------------


def test_rd_a1_invariant_holds_through_search_pipeline() -> None:
    """Gate-red reproduction: RD-A1 -- no item with known_at > as_of is
    ever returned by search(), across a mixed past/boundary/future set."""
    items = [
        _item(known_at=datetime(2024, 1, 1, tzinfo=timezone.utc)),
        _item(known_at=datetime(2025, 6, 15, tzinfo=timezone.utc)),
        _item(known_at=datetime(2026, 6, 15, tzinfo=timezone.utc)),  # == as_of
        _item(known_at=datetime(2026, 7, 1, tzinfo=timezone.utc)),  # > as_of
        _item(known_at=datetime(2027, 1, 1, tzinfo=timezone.utc)),  # > as_of
    ]

    as_of = datetime(2026, 6, 15, tzinfo=timezone.utc)
    result = search(items, as_of=as_of)

    for item in result:
        assert item.known_at <= as_of, (
            f"RD-A1 violated: item {item.item_id} known_at={item.known_at} > as_of={as_of}"
        )
    assert len(result) == 3
