"""IND-12 — catalog/registry_tiers.py: 코어·OSS·스크립트 3층 레지스트리.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.9 IND-12
(선행 IND-10/task-1729).

순수 모듈 — I/O 없음. CORE 층은 IND-10 `TALIB_SPECS`/`TALIB_GROUPS`(L01)를
그대로 소비한다(재생성 금지, 중복 금지 원칙). OSS 층은 이 leaf에 실제 소스가
아직 없어 빈 매핑이 기본이다(후속 leaf가 채운다). SCRIPT 층은 DSL-12
(task-1535) `script_hash`를 새 해시 체계 없이 그대로 버전·해시로 재사용한다
— 이 모듈은 스크립트를 컴파일·저장하지 않고, 호출자가 이미 컴파일된 항목
(`ScriptIndicatorEntry`)을 건넨다. 저장소 접근(`ScriptIndicatorSource`
Protocol)의 실제 구현은 이 leaf 범위 밖(custom/dsl_indicator.py, 후속) —
지금은 API 라우터가 빈 소스로 배선해 두고, 스크립트 지표가 실제로 저장되는
시점에 어댑터만 바꾸면 된다.

이름 충돌은 고정 우선순위 CORE > OSS > SCRIPT로 결정론적으로 해소한다 —
상위 층이 이미 쓰는 이름이면 하위 층 항목은 카탈로그에서 조용히 가려진다
(에러 아님, 커뮤니티 카탈로그의 통상 규칙). 같은 층 안의 이름 유일성은
호출자가 넘긴 `Mapping`이 이미 보장한다.

per-entry 해시(`CatalogEntry.entry_hash`)는 `registry.py`의
`canonical_spec_dict`(L02, IND-1)를 그대로 재사용해 sha256한다 — 전체
레지스트리 해시(`IndicatorRegistry.registry_hash()`)와 달리 지표 하나가
바뀌었는지만 보고 싶을 때 쓴다. SCRIPT 층은 이미 해시인 `script_hash`를
이중 해시하지 않는다(중복 해시 체계 금지, decision).
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
    """카탈로그 우선순위(선언 순서 = 이름 충돌 해소 순서, CORE가 최상위)."""

    CORE = "core"
    OSS = "oss"
    SCRIPT = "script"


@dataclass(frozen=True)
class ScriptIndicatorEntry:
    """SCRIPT 층 항목 하나. 테넌트 스코프는 `tenant_id`로 표현하고
    `merge_script_entries`가 요청 테넌트와 비교해 필터링한다(교차 테넌트
    미노출, §9.9 negative)."""

    name: str
    tenant_id: UUID
    spec: IndicatorSpec
    script_hash: str
    category: str = _SCRIPT_CATEGORY


class ScriptIndicatorSource(Protocol):
    """스크립트 지표 저장소 SPI — 구현은 이 leaf 범위 밖(향후
    custom/dsl_indicator.py). I/O 어댑터만 이 Protocol을 구현한다."""

    def list_for_tenant(self, tenant_id: UUID) -> Sequence[ScriptIndicatorEntry]: ...


@dataclass(frozen=True)
class CatalogEntry:
    """카탈로그 목록 항목 하나 — 3층 이름 충돌 해소 후의 최종 표시 단위."""

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
    """CORE + OSS 두 층만 합친 정적 카탈로그(테넌트 무관, 프로세스 1회
    계산·캐시 가능). SCRIPT 층은 요청마다 달라지므로(테넌트 스코프)
    `merge_script_entries`가 이 결과 위에 얹는다."""
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
            continue  # CORE가 이미 이름을 씀 — 결정론적으로 CORE 우선
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
    """정적 카탈로그(CORE/OSS) 위에 요청 테넌트가 볼 수 있는 SCRIPT 항목만
    얹는다. 이름이 이미 CORE/OSS에 있으면 SCRIPT는 가려진다(3층 우선순위
    그대로). 다른 테넌트 소유 항목은 애초에 이 dict에 들어오지 않는다."""
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
    """이름 오름차순 정렬 + 검색(이름 부분일치, 대소문자 무시) + 카테고리
    필터. 정렬은 커서 페이지네이션(`paginate_catalog`)의 전제조건이다."""
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
    """키셋 페이지네이션 — `cursor`는 직전 페이지 마지막 항목의 이름(정렬
    기준과 동일)이고, `entries`는 이미 이름순 정렬돼 있어야 한다(호출자
    책임, `list_catalog`가 보장). 커서보다 큰 첫 항목부터 이어간다 — 그
    이름이 목록에서 사라졌어도(개편) 커서 자체는 깨지지 않는다."""
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
