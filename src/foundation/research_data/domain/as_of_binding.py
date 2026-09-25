"""RD-9 -- domain/as_of_binding.py: `as_of` auto-binding for script-side
`research.*` queries + future-reference rejection.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-9
("expose `research.*` queries to scripts with `as_of` auto-binding +
leakage tests"), §4 RD-A1 ("an item with `known_at > as_of` is never
returned by any read path").

A DSL script has no clock of its own -- the only notion of "now" it can
legitimately use is the timestamp of the bar currently being evaluated
(`bar_ts`, from `CandleColumns.ts[i]`). This module is the single point
that resolves the effective `as_of` for a script-side research query and
enforces RD-A1 at the binding boundary, before the query even reaches
`application/query.search`: a script that tries to pass an explicit
`as_of` later than `bar_ts` is rejected fail-closed (not clamped -- silent
clamping would let a buggy script believe it saw data it did not).

Pure module -- no I/O (TID251, research_data/domain zone).
"""

from __future__ import annotations

from datetime import datetime

__all__ = ["AsOfBindingError", "bind_as_of"]


class AsOfBindingError(ValueError):
    """`RD_POINT_IN_TIME_VIOLATION` at the script-binding boundary -- a
    `research.*` call inside a script requested an `as_of` later than the
    current bar's timestamp (look-ahead attempt, RD-A1)."""


def bind_as_of(bar_ts: datetime, requested_as_of: datetime | None = None) -> datetime:
    """Resolve the effective `as_of` for a `research.*` call evaluated at `bar_ts`.

    Parameters
    ----------
    bar_ts:
        The timestamp of the bar currently being evaluated -- the script's
        only clock. Must be tz-aware.
    requested_as_of:
        An explicit `as_of` the script passed to the call, if any. When
        omitted (`None`), the call auto-binds to `bar_ts`.

    Returns
    -------
    datetime
        `bar_ts` when `requested_as_of` is omitted, otherwise
        `requested_as_of` -- but only if it does not reach past `bar_ts`.

    Raises
    ------
    ValueError:
        If either datetime is naive (tzinfo is None).
    AsOfBindingError:
        If `requested_as_of` is later than `bar_ts` (future reference).
    """
    if bar_ts.tzinfo is None:
        raise ValueError("bar_ts must be tz-aware (naive datetime rejected)")
    if requested_as_of is None:
        return bar_ts
    if requested_as_of.tzinfo is None:
        raise ValueError("requested_as_of must be tz-aware (naive datetime rejected)")
    if requested_as_of > bar_ts:
        raise AsOfBindingError(
            f"research.* as_of ({requested_as_of.isoformat()}) is later than "
            f"the current bar ({bar_ts.isoformat()}) -- future reference rejected"
        )
    return requested_as_of
