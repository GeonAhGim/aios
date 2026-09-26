"""IND-12 — `catalog/registry_tiers.py` 계약 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.9 IND-12.
DoD: 3층(코어>OSS>스크립트) 이름 충돌 결정론적 해소, 버전·해시 노출, 검색/
카테고리/커서 페이지네이션. 순수 함수만 대상 — I/O·앱 임포트 없음.

D2 증빙 보강(task-2926, DEEPEN 1730 — docs/audit/DEPTH_DSL_IND.md): 원 커밋
934b8d1은 negative 3건이 행위 단언뿐(명시적 `pytest.raises` 0건)이었고 실패
주입·수치 성능 단언·게이트 적색 재현이 없어 ADR-2026-09-09-C Decision 1의 D2
하한에 미달이었다. 이 파일 하단 4개 섹션이 그 증빙이다.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid

import pytest

import src.core.indicators.catalog.registry_tiers as registry_tiers_module
from scripts.check_import_linter import ROOT as LINTER_ROOT
from scripts.check_import_linter import _eval_forbidden, _imports_of, parse_contracts
from src.core.indicators.catalog.registry_tiers import (
    DEFAULT_STATIC_CATALOG,
    CatalogEntry,
    ScriptIndicatorEntry,
    Tier,
    build_static_catalog,
    chain_entry_hash,
    list_catalog,
    merge_script_entries,
    paginate_catalog,
)
from src.core.indicators.registry import (
    MAX_CHAIN_DEPTH,
    ChainNode,
    ColumnSource,
    IndicatorError,
    IndicatorRegistry,
    NodeSource,
    canonical_spec_dict,
)
from src.core.indicators.spec import REGISTRY_VERSION, IndicatorSpec, PlotSpec
from src.core.indicators.specs_talib import TALIB_SPECS


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
        static,
        [_script_entry("SCRIPT_Y", tenant_id=tenant_id, script_hash="a" * 64)],
        tenant_id=tenant_id,
    )
    assert merged["SCRIPT_Y"].tier == Tier.SCRIPT
    assert merged["SCRIPT_Y"].version == "a" * 64
    assert merged["SCRIPT_Y"].entry_hash == "a" * 64  # script_hash 재사용, 이중 해시 없음


def test_script_entry_hidden_by_core_name_conflict() -> None:
    static = build_static_catalog({"SMA": _spec("SMA")}, {})
    tenant_id = uuid.uuid4()
    merged = merge_script_entries(
        static,
        [_script_entry("SMA", tenant_id=tenant_id, script_hash="b" * 64)],
        tenant_id=tenant_id,
    )
    assert merged["SMA"].tier == Tier.CORE


def test_script_entry_from_other_tenant_is_excluded() -> None:
    static = build_static_catalog({}, {})
    owner_id, requester_id = uuid.uuid4(), uuid.uuid4()
    merged = merge_script_entries(
        static,
        [_script_entry("PRIVATE", tenant_id=owner_id, script_hash="c" * 64)],
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


# --- DEFAULT_STATIC_CATALOG: 설치된 TA-Lib 전 종 배선 확인 ---------------------


def test_default_static_catalog_covers_all_talib_indicators_with_no_duplicates() -> None:
    assert set(DEFAULT_STATIC_CATALOG) == set(TALIB_SPECS)
    assert len(DEFAULT_STATIC_CATALOG) == len(TALIB_SPECS)
    assert all(entry.tier == Tier.CORE for entry in DEFAULT_STATIC_CATALOG.values())


@pytest.mark.parametrize("name", ["SMA", "RSI", "MACD"])
def test_default_static_catalog_entries_have_category_and_hash(name: str) -> None:
    entry = DEFAULT_STATIC_CATALOG[name]
    assert entry.category  # TALIB_GROUPS에서 도출된 비어있지 않은 카테고리
    assert len(entry.entry_hash) == 64
    assert entry.version == REGISTRY_VERSION


# --- D2 명시 거부(pytest.raises): chain_entry_hash는 IndicatorRegistry의
# fail-closed 검증(resolve_chain)을 그대로 통과시킨다 — 계산 불가능한 그래프를
# 조용히 해시하지 않는다는 모듈 docstring의 주장을 실제로 증명한다. ----------


def _registry(*names: str) -> IndicatorRegistry:
    return IndicatorRegistry({name: _spec(name) for name in names})


def test_chain_entry_hash_rejects_unknown_node_reference() -> None:
    registry = _registry("A")
    graph = {
        "root": ChainNode(
            name="A", params={}, inputs={"close": NodeSource(node="missing", output="value")}
        ),
    }
    with pytest.raises(IndicatorError) as excinfo:
        chain_entry_hash("root", graph, registry)
    assert excinfo.value.code == "INDICATOR_INPUT_INVALID"


def test_chain_entry_hash_rejects_self_referential_cycle() -> None:
    registry = _registry("A")
    graph = {
        "root": ChainNode(
            name="A", params={}, inputs={"close": NodeSource(node="root", output="value")}
        ),
    }
    with pytest.raises(IndicatorError) as excinfo:
        chain_entry_hash("root", graph, registry)
    assert excinfo.value.code == "INDICATOR_CHAIN_CYCLE"


def test_chain_entry_hash_rejects_chain_deeper_than_max_depth() -> None:
    registry = _registry("A")
    node_count = MAX_CHAIN_DEPTH + 2
    graph: dict[str, ChainNode] = {
        "n0": ChainNode(name="A", params={}, inputs={"close": ColumnSource(column="close")}),
    }
    for i in range(1, node_count):
        graph[f"n{i}"] = ChainNode(
            name="A", params={}, inputs={"close": NodeSource(node=f"n{i - 1}", output="value")}
        )
    root = f"n{node_count - 1}"
    with pytest.raises(IndicatorError) as excinfo:
        chain_entry_hash(root, graph, registry)
    assert excinfo.value.code == "INDICATOR_CHAIN_TOO_DEEP"


# --- D2 실패 주입 --------------------------------------------------------------


def test_build_static_catalog_propagates_entry_hash_failure_instead_of_dropping_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`build_static_catalog`'s only real collaborator for per-entry hashing
    is `canonical_spec_dict` (registry.py, reused per this module's own
    docstring — IND-1 SSOT, no duplicate hash scheme). If that collaborator
    broke for a single indicator (a spec field that stopped being JSON-
    serializable after some upstream drift) and the build loop swallowed the
    error and moved on to the next name, `GET /v1/indicators` would silently
    ship a catalog missing that one indicator instead of failing outright —
    a name would simply vanish from the listing rather than surface as an
    error. Injecting the failure into the real collaborator (not a hand-built
    bad `IndicatorSpec`) proves no such silent per-entry skip exists."""
    real_canonical_spec_dict = registry_tiers_module.canonical_spec_dict

    def _flaky(name: str, spec: IndicatorSpec) -> dict[str, object]:
        if name == "B":
            raise RuntimeError("simulated canonical_spec_dict serialization failure")
        return real_canonical_spec_dict(name, spec)

    monkeypatch.setattr(registry_tiers_module, "canonical_spec_dict", _flaky)

    with pytest.raises(RuntimeError, match="simulated canonical_spec_dict"):
        build_static_catalog({"A": _spec("A"), "B": _spec("B")}, {})


# --- D2 성능 단언(페이지네이션/병합 지연) --------------------------------------


@pytest.mark.perf
def test_merge_and_paginate_latency_budget_matches_list_api_p95() -> None:
    """§9.9 IND-12 DoD requires `GET /v1/indicators` p95 <= 200ms.
    `merge_script_entries` + `list_catalog` + `paginate_catalog` are the pure,
    in-process cost that endpoint pays on every request (the router's own I/O
    — SCRIPT-tier source lookup — is out of this leaf's scope); this pins the
    pure part's share of that budget so it cannot quietly regress into eating
    the endpoint's whole latency budget on its own. 500 script entries for a
    single tenant simulates a very large marketplace catalog merged on top of
    every real CORE indicator of the installed TA-Lib."""
    tenant_id = uuid.uuid4()
    script_entries = [
        _script_entry(f"SCRIPT_{i:04d}", tenant_id=tenant_id, script_hash=f"{i:064x}")
        for i in range(500)
    ]
    repeats = 50
    start = time.perf_counter()
    for _ in range(repeats):
        merged = merge_script_entries(DEFAULT_STATIC_CATALOG, script_entries, tenant_id=tenant_id)
        entries = list_catalog(merged, q="SCRIPT")
        paginate_catalog(entries, cursor=None, limit=50)
    elapsed_ms = (time.perf_counter() - start) * 1000
    per_call_ms = elapsed_ms / repeats
    budget_ms = 50.0  # 200ms p95 엔드포인트 예산 중 순수 부분에 배정한 몫
    assert per_call_ms < budget_ms, (
        f"merge_script_entries+list_catalog+paginate_catalog took "
        f"{per_call_ms:.2f}ms/call over {repeats} reps, budget {budget_ms}ms"
    )


# --- D2 게이트 적색 재현 -------------------------------------------------------


def test_import_linter_core_no_io_catches_registry_tiers_foundation_import_regression() -> None:
    """This module's own docstring states it is pure — "순수 모듈 — I/O 없음"
    — exactly the invariant `.importlinter`'s `core-no-io` forbidden contract
    enforces (`src.core` may not import `src.exchanges`/`src.api`/
    `src.foundation`). Proves that contract's real evaluator
    (`scripts/check_import_linter.py`) fires red for a synthetic regression
    shape (this module importing a foundation module to reach marketplace
    persistence directly, say), and stays green for the module's real current
    imports — using a synthetic graph so the test doesn't require the
    regression to exist in the tree first."""
    contracts = parse_contracts(LINTER_ROOT / ".importlinter")
    core_no_io = next(c for c in contracts if c["id"] == "core-no-io")

    module = "src.core.indicators.catalog.registry_tiers"
    regressed_graph = {module: {"src.foundation.marketplace.persistence"}}
    hits = _eval_forbidden(regressed_graph, core_no_io)
    assert len(hits) == 1
    assert hits[0][0] == module

    real_imports = _imports_of(
        LINTER_ROOT / "src/core/indicators/catalog/registry_tiers.py", module, is_package=False
    )
    assert _eval_forbidden({module: real_imports}, core_no_io) == []
