"""IND-12 — catalog/registry_tiers.py: Core/OSS/script three-tier registry.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.9 IND-12
(prerequisite IND-10/task-1729).

Pure module — no I/O. The CORE tier consumes IND-10 ``TALIB_SPECS``/``TALIB_GROUPS``
(L01) as-is (regeneration prohibited, deduplication required). The OSS tier defaults
to an empty mapping because its actual source does not yet exist in this leaf
(subsequent leaf will populate it). The SCRIPT tier reuses the DSL-12
(task-1535) ``script_hash`` as-is for versioning and hashing without introducing a
new hash scheme — this module does not compile or store scripts; the caller passes
already-compiled items (``ScriptIndicatorEntry``). The actual implementation of
storage access (``ScriptIndicatorSource`` Protocol) lives outside this leaf scope
(custom/dsl_indicator.py, subsequent) — for now the API router is wired with an
empty source, and the adapter can be swapped when script indicators are actually
persisted.

Name collisions are resolved deterministically with fixed priority CORE > OSS >
SCRIPT — if a name is already used by an upper tier, the lower-tier entry is
silently masked in the catalog (not an error, standard practice for community
catalogs). Intra-tier name uniqueness is already guaranteed by the ``Mapping``
passed by the caller.

Per-entry hash (``CatalogEntry.entry_hash``) sha256es ``canonical_spec_dict`` from
``registry.py`` (L02, IND-1) as-is — used when you only want to know whether a
single indicator changed, unlike the full registry hash
(``IndicatorRegistry.registry_hash()``). The SCRIPT tier does not double-hash
``script_hash`` (duplicate hash schemes prohibited, decision).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol
from uuid import UUID

from src.core.indicators.generate_specs import TALIB_GROUPS
from src.core.indicators.registry import (
    ChainGraph,
    ColumnSource,
    IndicatorRegistry,
    canonical_spec_dict,
    resolve_chain,
)
from src.core.indicators.spec import REGISTRY_VERSION, IndicatorSpec
from src.core.indicators.specs_talib import TALIB_SPECS

__all__ = [
    "CatalogEntry",
    "DEFAULT_STATIC_CATALOG",
    "ScriptIndicatorEntry",
    "ScriptIndicatorSource",
    "Tier",
    "build_static_catalog",
    "chain_entry_hash",
    "list_catalog",
    "merge_script_entries",
    "paginate_catalog",
]

_UNCATEGORIZED = "uncategorized"
_SCRIPT_CATEGORY = "script"


class Tier(str, Enum):
    """Catalog priority order (declaration order = name collision resolution order, CORE is top)."""

    CORE = "core"
    OSS = "oss"
    SCRIPT = "script"


@dataclass(frozen=True)
class ScriptIndicatorEntry:
    """One SCRIPT-tier entry. Tenant scope is expressed via `tenant_id` and
    `merge_script_entries` filters by comparing against the requested tenant
    (cross-tenant non-exposure, §9.9 negative)."""

    name: str
    tenant_id: UUID
    spec: IndicatorSpec
    script_hash: str
    category: str = _SCRIPT_CATEGORY


class ScriptIndicatorSource(Protocol):
    """Script indicator storage SPI — implementation is outside this leaf scope
    (future custom/dsl_indicator.py). Only I/O adapters implement this Protocol."""

    def list_for_tenant(self, tenant_id: UUID) -> Sequence[ScriptIndicatorEntry]: ...


@dataclass(frozen=True)
class CatalogEntry:
    """One catalog list item — final display unit after 3-tier name collision resolution."""

    name: str
    tier: Tier
    category: str
    version: str
    entry_hash: str
    spec: IndicatorSpec


def _spec_entry_hash(name: str, spec: IndicatorSpec) -> str:
    payload = json.dumps(canonical_spec_dict(name, spec), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_static_catalog(
    core_specs: Mapping[str, IndicatorSpec],
    core_categories: Mapping[str, str],
    *,
    oss_specs: Mapping[str, IndicatorSpec] | None = None,
    oss_categories: Mapping[str, str] | None = None,
) -> dict[str, CatalogEntry]:
    """Static catalog combining only CORE + OSS two tiers (tenant-independent,
    computed once per process, cacheable). The SCRIPT tier varies per request
    (tenant scope), so `merge_script_entries` layers on top of this result."""
    catalog: dict[str, CatalogEntry] = {}
    for name, spec in core_specs.items():
        catalog[name] = CatalogEntry(
            name=name,
            tier=Tier.CORE,
            category=core_categories.get(name, _UNCATEGORIZED),
            version=REGISTRY_VERSION,
            entry_hash=_spec_entry_hash(name, spec),
            spec=spec,
        )
    for name, spec in (oss_specs or {}).items():
        if name in catalog:
            continue  # CORE already uses this name — deterministically CORE-first
        catalog[name] = CatalogEntry(
            name=name,
            tier=Tier.OSS,
            category=(oss_categories or {}).get(name, "oss"),
            version=REGISTRY_VERSION,
            entry_hash=_spec_entry_hash(name, spec),
            spec=spec,
        )
    return catalog


def merge_script_entries(
    static_catalog: Mapping[str, CatalogEntry],
    script_entries: Sequence[ScriptIndicatorEntry],
    *,
    tenant_id: UUID,
) -> dict[str, CatalogEntry]:
    """Layer only SCRIPT entries the requesting tenant can see on top of the
    static catalog (CORE/OSS). If a name already exists in CORE/OSS, the
    SCRIPT entry is masked (3-tier priority preserved). Items owned by other
    tenants never enter this dict in the first place."""
    merged = dict(static_catalog)
    for entry in script_entries:
        if entry.tenant_id != tenant_id:
            continue
        if entry.name in merged:
            continue
        merged[entry.name] = CatalogEntry(
            name=entry.name,
            tier=Tier.SCRIPT,
            category=entry.category,
            version=entry.script_hash,
            entry_hash=entry.script_hash,
            spec=entry.spec,
        )
    return merged


def list_catalog(
    catalog: Mapping[str, CatalogEntry],
    *,
    q: str | None = None,
    category: str | None = None,
) -> list[CatalogEntry]:
    """Name ascending sort + search (partial name match, case-insensitive) +
    category filter. Sorting is a precondition for cursor pagination
    (`paginate_catalog`)."""
    needle = q.strip().lower() if q else None
    entries = [
        entry
        for entry in catalog.values()
        if (needle is None or needle in entry.name.lower())
        and (category is None or entry.category == category)
    ]
    entries.sort(key=lambda entry: entry.name)
    return entries


def paginate_catalog(
    entries: Sequence[CatalogEntry], *, cursor: str | None, limit: int
) -> tuple[list[CatalogEntry], str | None]:
    """Keyset pagination — `cursor` is the name of the last item in the
    previous page (same sort order as `entries`, which must already be sorted
    by name (caller's responsibility, guaranteed by `list_catalog`). Continues
    from the first item whose name is greater than the cursor — the cursor
    itself does not break even if that name has disappeared from the list
    (reorg)."""
    start = len(entries)
    for i, entry in enumerate(entries):
        if cursor is None or entry.name > cursor:
            start = i
            break
    page = list(entries[start : start + limit])
    has_more = start + limit < len(entries)
    next_cursor = page[-1].name if page and has_more else None
    return page, next_cursor


DEFAULT_STATIC_CATALOG: dict[str, CatalogEntry] = build_static_catalog(TALIB_SPECS, TALIB_GROUPS)


def chain_entry_hash(root: str, graph: ChainGraph, registry: IndicatorRegistry) -> str:
    """Content hash for an IND-16 chain-composed catalog entry.

    Reuses the same `canonical_spec_dict`/sha256 recipe as `_spec_entry_hash`,
    extended with the dependency graph's shape (node ids, params, input
    wiring) — a chain's hash must change if any upstream node's definition
    changes, even when the root indicator's own spec is untouched. Validates
    `graph` first (`resolve_chain` raises `IndicatorError` fail-closed on an
    unknown node, a cycle, or an over-deep chain) so this never hashes a
    graph that couldn't actually be computed.
    """
    order, _ = resolve_chain(graph, root, registry)
    canonical = [
        {
            "node": node_id,
            "spec": canonical_spec_dict(graph[node_id].name, registry.get(graph[node_id].name)),
            "params": dict(sorted(graph[node_id].params.items())),
            "inputs": {
                input_name: (
                    {"column": source.column}
                    if isinstance(source, ColumnSource)
                    else {"node": source.node, "output": source.output}
                )
                for input_name, source in sorted(graph[node_id].inputs.items())
            },
        }
        for node_id in order
    ]
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
