"""RD-9 -- adapters/dsl_query.py: `research.*` built-in functions bridging
AIOS Script (DSL) to `research_data` point-in-time queries.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-9
(depends on RD-7 `application/query.search`, DSL-9 `runtime/builtins_ta.py`
registration pattern).

Follows the DSL-9 builtin convention (`ns.ident(...)` dispatched from a
host-injected `(ns, ident) -> Builtin` table, `builtins_ta.py`
`TaBuiltins`/`default_builtins`): this module owns the `research`
namespace and is merged into the builtin registry the same way `ta.*` is.

Grammar gap (honest limitation, not a guess): AIOS Script has no string
literal type yet (`grammar/ast.py` defines no `StringLiteral` node), so a
script cannot pass an instrument id or item kind as a call argument. This
leaf therefore exposes one zero-argument builtin per `ResearchItemKind`
(`research.filing_count()`, `research.news_count()`, `research.macro_count()`,
`research.alt_count()`) scoped to the single instrument the backtest run
is already bound to (host-supplied at builtin-table construction time, the
same way `ta.*` is bound to a fixed `IndicatorRegistry` -- not something
the script chooses). Parameterising by instrument/kind from script text is
a DSL-4 grammar leaf, out of this leaf's scope.

Point-in-time integrity (RD-A1, "known_at > as_of is never returned by any
read path"): every bar's query is bound to that bar's own timestamp via
`domain/as_of_binding.bind_as_of` -- there is no way to request a later
`as_of`, because this bridge never accepts one from the script (no
argument carries it). A script cannot construct a future reference; the
compiler/runtime rejection the spec DoD asks for is structural here, not a
checked-exception path (there is nothing to check -- the call shape makes
the leak unrepresentable).

Pure w.r.t. I/O in the TID251 sense (no network/db calls here) -- `items`
(the candidate `ResearchItem`s) must already be fetched by the caller
before constructing this builtin table (matching `application/query.search`'s
input contract, and how `TaBuiltins` receives a repository-free
`IndicatorRegistry`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from src.core.script.runtime.series import ScriptRuntimeError, Series, Value
from src.foundation.market_data.api import CandleColumns
from src.foundation.research_data.application.query import search
from src.foundation.research_data.contracts.v1 import ResearchItem, ResearchItemKind
from src.foundation.research_data.domain.as_of_binding import bind_as_of

if TYPE_CHECKING:
    from src.core.script.runtime.interpreter_types import Builtin, CallSite

__all__ = ["ResearchDslQueryError", "research_builtins"]

_NS: Final = "research"
_KIND_IDENTS: Final[Mapping[ResearchItemKind, str]] = {
    "filing": "filing_count",
    "news": "news_count",
    "macro": "macro_count",
    "alt": "alt_count",
}


class ResearchDslQueryError(ScriptRuntimeError):
    """`research.*` builtin call rejected -- wrong arity, or a columns/
    bar_count mismatch (fail-closed, mirrors `ScriptSignalSourceError`)."""


@dataclass(frozen=True, slots=True)
class _KnownCountBuiltin:
    """One `research.<kind>_count()` builtin, closed over the candidate
    `items`/`columns` at construction time (DSL-9 pattern: the callable
    itself carries no host state beyond what it was built with)."""

    kind: ResearchItemKind
    instrument: str
    items: Sequence[ResearchItem]
    columns: CandleColumns

    def __call__(self, args: tuple[Value, ...], site: CallSite) -> Value:
        if args:
            raise ResearchDslQueryError(
                f"research.{_KIND_IDENTS[self.kind]}() takes no arguments "
                f"(received {len(args)}) -- instrument/kind are host-bound, "
                "not script-selectable (grammar has no string literal yet)"
            )
        if len(self.columns) != site.bar_count:
            raise ResearchDslQueryError(
                f"columns length ({len(self.columns)}) differs from "
                f"bar_count ({site.bar_count})"
            )
        counts: list[float] = []
        for i in range(site.bar_count):
            as_of = bind_as_of(self.columns.ts[i])
            visible = search(
                self.items,
                instruments=[self.instrument],
                kinds=[self.kind],
                as_of=as_of,
            )
            counts.append(float(len(visible)))
        return Series.of_floats(counts)


def research_builtins(
    items: Sequence[ResearchItem],
    columns: CandleColumns,
    *,
    instrument: str,
) -> dict[tuple[str, str], Builtin]:
    """Build the `research.*` builtin table for one backtest run.

    `items` is the full candidate set already fetched for `instrument` (the
    caller's repository read, e.g. RD-4 `postgres_repository`) -- this
    function does no I/O of its own. `columns` supplies the per-bar `ts`
    that `as_of` auto-binds to (§9 RD-9 "as_of auto-binding").
    """
    return {
        (_NS, ident): _KnownCountBuiltin(
            kind=kind, instrument=instrument, items=items, columns=columns
        )
        for kind, ident in _KIND_IDENTS.items()
    }
