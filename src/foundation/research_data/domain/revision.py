"""RD-3 -- domain/revision.py: revision chain rules (pure).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §2.1
domain/revision.py, §4 RD-A2 (append-only -- a correction is a new row
linked by `revision_of`, never an in-place edit), §9 RD-3 (c)(d).

A revision family is the set of `ResearchItem`s that all trace back (via
`revision_of`) to the same original filing/article. This module only
answers one question about that set: does it form a single unbranched
chain from the original to the latest correction, or does the input
contain a cycle or a fork? Both are rejected explicitly (RD-3 DoD (c)) --
there is no "best effort" ordering for malformed input.

`known_at` ordering along the chain is checked via RD-2's
`known_at.assert_point_in_time` (which itself delegates to FA-9
`core/bitemporal.py`) -- RD-3 DoD (d) forbids reimplementing that
comparison here, so a later revision's `known_at` is validated by treating
the earlier revision as the "item" and the later revision's `known_at` as
the `as_of_time` probe.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from uuid import UUID

from src.foundation.research_data.contracts.v1 import ResearchItem
from src.foundation.research_data.domain.known_at import assert_point_in_time

__all__ = [
    "RD_REVISION_CYCLE",
    "RD_REVISION_BRANCH",
    "RevisionChainError",
    "RevisionCycleError",
    "RevisionBranchError",
    "link_revision_chain",
]

RD_REVISION_CYCLE = "RD_REVISION_CYCLE"
RD_REVISION_BRANCH = "RD_REVISION_BRANCH"


class RevisionChainError(ValueError):
    """Base for RD-3 revision-chain violations. Not retryable -- the caller
    must fix the input set, not resend it."""


class RevisionBranchError(RevisionChainError):
    """`RD_REVISION_BRANCH` -- one item is superseded by more than one other
    item. A revision chain must stay single-strand (§9 RD-3 (c))."""

    error_code = RD_REVISION_BRANCH

    def __init__(self, parent_id: UUID, children_ids: Sequence[UUID]) -> None:
        self.parent_id = parent_id
        self.children_ids = tuple(children_ids)
        super().__init__(
            f"{RD_REVISION_BRANCH}: item_id={parent_id} is superseded by "
            f"{len(self.children_ids)} items {self.children_ids} -- a "
            "revision chain must stay single-strand"
        )


class RevisionCycleError(RevisionChainError):
    """`RD_REVISION_CYCLE` -- the `revision_of` links among the given items
    do not resolve to exactly one reachable origin (a loop, a dangling
    reference, or more than one root passed together)."""

    error_code = RD_REVISION_CYCLE

    def __init__(self, item_ids: Sequence[UUID]) -> None:
        self.item_ids = tuple(item_ids)
        super().__init__(
            f"{RD_REVISION_CYCLE}: revision_of links among {self.item_ids} "
            "do not resolve to a single reachable origin"
        )


def link_revision_chain(items: Sequence[ResearchItem]) -> tuple[ResearchItem, ...]:
    """Order one revision family from origin to latest, or raise.

    `items` must be exactly one family (every item's `revision_of` chain
    bottoms out within this same set, or is `None`). Returns the items
    ordered oldest-first. Raises `RevisionBranchError` if any item is
    superseded by more than one other item, or `RevisionCycleError` if the
    links do not resolve to exactly one reachable origin covering every
    item passed in.
    """
    if not items:
        return ()

    by_id = {item.item_id: item for item in items}
    successors: dict[UUID | None, list[UUID]] = defaultdict(list)
    for item in items:
        successors[item.revision_of].append(item.item_id)

    for parent_id, child_ids in successors.items():
        if parent_id is not None and len(child_ids) > 1:
            raise RevisionBranchError(parent_id, child_ids)

    roots = successors.get(None, [])
    if len(roots) != 1:
        raise RevisionCycleError(tuple(by_id))

    ordered: list[ResearchItem] = []
    visited: set[UUID] = set()
    current_id: UUID | None = roots[0]
    while current_id is not None:
        if current_id in visited:
            raise RevisionCycleError(tuple(by_id))
        visited.add(current_id)
        current = by_id[current_id]
        if ordered:
            assert_point_in_time(ordered[-1], current.known_at)
        ordered.append(current)
        next_ids = successors.get(current_id, [])
        current_id = next_ids[0] if next_ids else None

    if len(ordered) != len(items):
        raise RevisionCycleError(tuple(by_id))

    return tuple(ordered)
