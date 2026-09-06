"""IND-12 — `catalog/registry_tiers.py` 계약 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.9 IND-12.
DoD: 3층(코어>OSS>스크립트) 이름 충돌 결정론적 해소, 버전·해시 노출, 검색/
카테고리/커서 페이지네이션. 순수 함수만 대상 — I/O·앱 임포트 없음.
"""
from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from src.core.indicators.catalog.registry_tiers import (
    DEFAULT_STATIC_CATALOG,
    CatalogEntry,
    ScriptIndicatorEntry,
    Tier,
    build_static_catalog,
    list_catalog,
    merge_script_entries,
    paginate_catalog,
)
from src.core.indicators.registry import canonical_spec_dict
from src.core.indicators.spec import REGISTRY_VERSION, IndicatorSpec, PlotSpec


def _spec(name: str) -> IndicatorSpec:
    return IndicatorSpec(
        name=name,
        inputs=("close",),
        params=(),
        outputs=("value",),
        lookback=lambda params: 0,
        plots=(PlotSpec(kind="line", scale="own", default_pane="separate"),),
    )


def _script_entry(name: str, *, tenant_id: uuid.UUID, script_hash: str) -> ScriptIndicatorEntry:
    return ScriptIndicatorEntry(
        name=name, tenant_id=tenant_id, spec=_spec(name), script_hash=script_hash
    )


# --- build_static_catalog: 이름 충돌 결정론 ---------------------------------


def test_core_beats_oss_on_name_conflict() -> None:
    core_spec, oss_spec = _spec("X"), _spec("X")
    catalog = build_static_catalog(
        {"X": core_spec},
        {"X": "core-cat"},
        oss_specs={"X": oss_spec},
        oss_categories={"X": "oss-cat"},
    )
    assert catalog["X"].tier == Tier.CORE
    assert catalog["X"].spec is core_spec
    assert catalog["X"].category == "core-cat"


def test_oss_added_when_no_name_conflict() -> None:
    catalog = build_static_catalog({"X": _spec("X")}, {}, oss_specs={"Y": _spec("Y")})
    assert catalog["Y"].tier == Tier.OSS
    assert catalog["Y"].category == "oss"  # 카테고리 미지정 시 기본값


def test_conflict_resolution_deterministic_regardless_of_mapping_order() -> None:
    core = {"A": _spec("A"), "B": _spec("B")}
    oss_forward = {"B": _spec("B_oss"), "C": _spec("C")}
    oss_backward = dict(reversed(list(oss_forward.items())))

    forward = build_static_catalog(core, {}, oss_specs=oss_forward)
    backward = build_static_catalog(core, {}, oss_specs=oss_backward)

    assert forward["B"].tier == backward["B"].tier == Tier.CORE
    assert forward["C"].tier == backward["C"].tier == Tier.OSS


def test_core_version_is_registry_version() -> None:
    catalog = build_static_catalog({"X": _spec("X")}, {})
    assert catalog["X"].version == REGISTRY_VERSION


def test_entry_hash_matches_canonical_spec_dict_sha256() -> None:
    spec = _spec("X")
    catalog = build_static_catalog({"X": spec}, {})
    expected_payload = json.dumps(
        canonical_spec_dict("X", spec), sort_keys=True, separators=(",", ":")
    )
    expected = hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()
    assert catalog["X"].entry_hash == expected
    assert len(catalog["X"].entry_hash) == 64


# --- merge_script_entries: 테넌트 스코프 + 3층 우선순위 ---------------------


def test_script_entry_added_when_no_conflict() -> None:
    static = build_static_catalog({"CORE_X": _spec("CORE_X")}, {})
    tenant_id = uuid.uuid4()
    merged = merge_script_entries(
        static, [_script_entry("SCRIPT_Y", tenant_id=tenant_id, script_hash="a" * 64)],
        tenant_id=tenant_id,
    )
    assert merged["SCRIPT_Y"].tier == Tier.SCRIPT
    assert merged["SCRIPT_Y"].version == "a" * 64
    assert merged["SCRIPT_Y"].entry_hash == "a" * 64  # script_hash 재사용, 이중 해시 없음


def test_script_entry_hidden_by_core_name_conflict() -> None:
    static = build_static_catalog({"SMA": _spec("SMA")}, {})
    tenant_id = uuid.uuid4()
    merged = merge_script_entries(
        static, [_script_entry("SMA", tenant_id=tenant_id, script_hash="b" * 64)],
        tenant_id=tenant_id,
    )
    assert merged["SMA"].tier == Tier.CORE


def test_script_entry_from_other_tenant_is_excluded() -> None:
    static = build_static_catalog({}, {})
    owner_id, requester_id = uuid.uuid4(), uuid.uuid4()
    merged = merge_script_entries(
        static, [_script_entry("PRIVATE", tenant_id=owner_id, script_hash="c" * 64)],
        tenant_id=requester_id,
    )
    assert "PRIVATE" not in merged


def test_static_catalog_untouched_by_merge() -> None:
    """`merge_script_entries`가 입력 dict를 변형하지 않는다(방어적 복사)."""
    static = build_static_catalog({"X": _spec("X")}, {})
    tenant_id = uuid.uuid4()
    merge_script_entries(
        static, [_script_entry("Y", tenant_id=tenant_id, script_hash="d" * 64)], tenant_id=tenant_id
    )
    assert "Y" not in static


# --- list_catalog: 검색·카테고리 필터 + 정렬 --------------------------------


def _catalog(*names: str) -> dict[str, CatalogEntry]:
    return build_static_catalog({name: _spec(name) for name in names}, {})


def test_list_catalog_sorted_by_name() -> None:
    entries = list_catalog(_catalog("C", "A", "B"))
    assert [e.name for e in entries] == ["A", "B", "C"]


def test_list_catalog_search_is_case_insensitive_substring() -> None:
    entries = list_catalog(_catalog("SMA", "EMA", "RSI"), q="ma")
    assert {e.name for e in entries} == {"SMA", "EMA"}


def test_list_catalog_category_filter() -> None:
    catalog = build_static_catalog(
        {"A": _spec("A"), "B": _spec("B")}, {"A": "trend", "B": "momentum"}
    )
    entries = list_catalog(catalog, category="trend")
    assert [e.name for e in entries] == ["A"]


# --- paginate_catalog: 커서 키셋 페이지네이션 -------------------------------


def test_paginate_catalog_first_page_and_next_cursor() -> None:
    entries = list_catalog(_catalog("A", "B", "C", "D"))
    page, next_cursor = paginate_catalog(entries, cursor=None, limit=2)
    assert [e.name for e in page] == ["A", "B"]
    assert next_cursor == "B"


def test_paginate_catalog_resumes_from_cursor() -> None:
    entries = list_catalog(_catalog("A", "B", "C", "D"))
    page, next_cursor = paginate_catalog(entries, cursor="B", limit=2)
    assert [e.name for e in page] == ["C", "D"]
    assert next_cursor is None


def test_paginate_catalog_stale_cursor_still_resumes_after_removed_name() -> None:
    """커서가 가리키던 이름이 카탈로그에서 사라져도(개편) 그보다 큰 첫
    항목부터 이어간다 — strict 재현 대신 관대한 재개."""
    entries = list_catalog(_catalog("A", "C", "D"))
    page, _ = paginate_catalog(entries, cursor="B", limit=10)
    assert [e.name for e in page] == ["C", "D"]


def test_paginate_catalog_empty_input() -> None:
    page, next_cursor = paginate_catalog([], cursor=None, limit=10)
    assert page == []
    assert next_cursor is None


# --- DEFAULT_STATIC_CATALOG: 실제 161종 배선 확인 ---------------------------


def test_default_static_catalog_covers_all_talib_indicators_with_no_duplicates() -> None:
    assert len(DEFAULT_STATIC_CATALOG) == 161
    assert all(entry.tier == Tier.CORE for entry in DEFAULT_STATIC_CATALOG.values())


@pytest.mark.parametrize("name", ["SMA", "RSI", "MACD"])
def test_default_static_catalog_entries_have_category_and_hash(name: str) -> None:
    entry = DEFAULT_STATIC_CATALOG[name]
    assert entry.category  # TALIB_GROUPS에서 도출된 비어있지 않은 카테고리
    assert len(entry.entry_hash) == 64
    assert entry.version == REGISTRY_VERSION
