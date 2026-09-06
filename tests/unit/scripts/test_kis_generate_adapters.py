"""scripts/kis_generate_adapters.py 단위 테스트 — BR-12(ADR-2026-09-06-I D7).

DoD: (1) 같은 기준 목록 + 같은 수기 파일이면 바이트 동일(결정론), (2) 파일당 300줄
상한, (3) 미지원 HTTP 메서드는 조용히 잘못된 코드를 만들지 않고 예외로 죽는다
(negative), (4) 예외 오버라이드 표가 기계 추출값보다 우선한다, (5) 저장소에 커밋된
`src/exchanges/kis/generated/`가 이 스크립트 + 기준 목록으로 재생성한 결과와 바이트
동일(드리프트 없음), (6) 재생성 후 `kis_tr_coverage`의 미착수가 0이 된다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gen = _load_module("kis_generate_adapters", SCRIPTS_DIR / "kis_generate_adapters.py")


def _row(
    tr_id: str,
    domain: str = "domestic_stock",
    method: str = "GET",
    path: str | None = "/uapi/domestic-stock/v1/quotations/inquire-price",
    params: list[dict[str, Any]] | None = None,
    containers: list[str] | None = None,
    fields: list[str] | None = None,
    source_path: str = "examples_llm/domestic_stock/inquire_price/inquire_price.py",
    label: str = "[국내주식] 기본시세 > 주식현재가 시세[v1_국내주식-008]",
) -> dict[str, Any]:
    return {
        "tr_id": tr_id,
        "domain": domain,
        "label": label,
        "source_path": source_path,
        "method": method,
        "path": path,
        "params": params if params is not None else [{"name": "FID_INPUT_ISCD", "required": True}],
        "response": {
            "kind": "ws" if method == "WS" else "rest",
            "containers": containers if containers is not None else ["output"],
            "fields": fields if fields is not None else [],
        },
        "extraction_error": None,
    }


_SAMPLE_REFERENCE = {
    "trs": [
        _row("TTTC0011U", domain="domestic_stock"),
        _row("VTTC0011U", domain="domestic_stock", label="[국내주식] 매수(모의)"),
        _row(
            "H0STCNT0",
            domain="domestic_stock",
            method="WS",
            path=None,
            params=[],
            containers=[],
            fields=["MKSC_SHRN_ISCD", "STCK_PRPR"],
            source_path="examples_user/domestic_stock/domestic_stock_functions_ws.py",
            label="[국내주식] 실시간체결가",
        ),
        _row(
            "CTSC8407R",
            domain="domestic_bond",
            method="POST",
            path="/uapi/domestic-bond/v1/trading/buy",
            params=[
                {"name": "CANO", "required": True},
                {"name": "ORD_QTY2", "required": False},
            ],
            source_path="examples_llm/domestic_bond/buy/buy.py",
            label="[장내채권] 매수",
        ),
    ]
}


# ---------------------------------------------------------------------------
# select_todo_rows — kis_tr_coverage.classify()를 그대로 재사용
# ---------------------------------------------------------------------------


def test_select_todo_rows_excludes_handwritten() -> None:
    handwritten = 'tr_id = "TTTC0011U"\n'  # 나머지는 미구현
    todo = gen.select_todo_rows(_SAMPLE_REFERENCE, handwritten)
    assert [r["tr_id"] for r in todo] == ["CTSC8407R", "H0STCNT0", "VTTC0011U"]


def test_select_todo_rows_sorted_by_tr_id() -> None:
    todo = gen.select_todo_rows(_SAMPLE_REFERENCE, handwritten_source="")
    assert [r["tr_id"] for r in todo] == ["CTSC8407R", "H0STCNT0", "TTTC0011U", "VTTC0011U"]


# ---------------------------------------------------------------------------
# 렌더러 — 정상 + negative(미지원 메서드)
# ---------------------------------------------------------------------------


def test_render_rest_method_embeds_literal_tr_id_path_and_method() -> None:
    lines = gen.render_rest_method(_row("FHKST01010100", method="GET"))
    text = "\n".join(lines)
    assert '"GET"' in text
    assert '"FHKST01010100"' in text
    assert '"/uapi/domestic-stock/v1/quotations/inquire-price"' in text
    assert "params=params or {}" in text


def test_render_rest_method_post_uses_body_kwarg() -> None:
    lines = gen.render_rest_method(_row("TTTC0952U", method="POST", path="/uapi/x"))
    text = "\n".join(lines)
    assert "body=params or {}" in text
    assert "params=params or {}" not in text


def test_is_order_method_true_for_post_false_for_get_and_ws() -> None:
    """review:1971 REJECT 후속(task-1975) — 주문성 판정은 method=POST 하나로
    충분하다(이 기준 목록의 POST 48건은 예외 없이 매수/매도/정정/취소/예약주문)."""
    assert gen.is_order_method(_row("TTTC0952U", method="POST", path="/uapi/x"))
    assert not gen.is_order_method(_row("FHKST01010100", method="GET"))
    assert not gen.is_order_method(_row("H0STCNT0", method="WS", path=None, params=[]))


def test_render_rest_method_post_gets_paper_sandbox_guard() -> None:
    """negative(BR-12 19건 무방비의 회귀 방지) — POST로 생성되는 메서드는
    반드시 `@require_paper_sandbox`를 방출해야 한다."""
    lines = gen.render_rest_method(_row("TTTC0952U", method="POST", path="/uapi/x"))
    assert "    @require_paper_sandbox" in lines


def test_render_rest_method_get_has_no_guard_decorator() -> None:
    lines = gen.render_rest_method(_row("FHKST01010100", method="GET"))
    assert "    @require_paper_sandbox" not in lines


def test_render_chunk_file_imports_guard_only_when_order_present() -> None:
    with_order = gen.render_chunk_file(
        "domestic_stock", 1, [_row("TTTC0952U", method="POST", path="/uapi/x")]
    )
    assert "from src.exchanges.common.live_guard import require_paper_sandbox" in with_order

    without_order = gen.render_chunk_file(
        "domestic_stock", 1, [_row("FHKST01010100", method="GET")]
    )
    assert "from src.exchanges.common.live_guard import require_paper_sandbox" not in without_order


def test_render_ws_method_embeds_tr_id_in_subscribe_call() -> None:
    row = _row("H0STCNT0", method="WS", path=None, params=[])
    lines = gen.render_ws_method(row)
    text = "\n".join(lines)
    assert '"H0STCNT0"' in text
    assert "_run_kis_ws_subscription" in text


def test_render_rest_method_rejects_unsupported_http_method() -> None:
    """negative — DELETE 같은 미지원 메서드는 조용히 잘못된 코드를 내지 않고 죽는다."""
    row = _row("XXXXXXXXX", method="DELETE")
    with pytest.raises(gen.KisGenerateError):
        gen.render_rest_method(row)


def test_render_ws_method_rejects_non_ws_row() -> None:
    """negative — WS 렌더러에 REST 행이 잘못 전달되면 예외로 죽는다."""
    row = _row("FHKST01010100", method="GET")
    with pytest.raises(gen.KisGenerateError):
        gen.render_ws_method(row)


def test_render_chunk_file_raises_if_over_line_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """negative — 라인 예산 점검(assert) 자체가 우회되지 않는지 회귀 고정."""
    monkeypatch.setattr(gen, "_FILE_LINE_CAP", 5)
    with pytest.raises(gen.KisGenerateError):
        gen.render_chunk_file("domestic_stock", 1, [_row("FHKST01010100")])


# ---------------------------------------------------------------------------
# 예외 오버라이드 — 기계 추출값보다 우선
# ---------------------------------------------------------------------------


def test_exception_override_wins_over_extracted_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        gen._EXCEPTION_OVERRIDES, "TTTC0011U", {"path": "/uapi/domestic-stock/v1/corrected"}
    )
    todo = gen.select_todo_rows(_SAMPLE_REFERENCE, handwritten_source="")
    row = next(r for r in todo if r["tr_id"] == "TTTC0011U")
    assert row["path"] == "/uapi/domestic-stock/v1/corrected"


# ---------------------------------------------------------------------------
# generate_files — 결정론 + 300줄 상한
# ---------------------------------------------------------------------------


def test_generate_files_is_byte_identical_for_same_input() -> None:
    first = gen.generate_files(_SAMPLE_REFERENCE, handwritten_source="")
    second = gen.generate_files(_SAMPLE_REFERENCE, handwritten_source="")
    assert first == second


def test_generate_files_respects_line_cap() -> None:
    files = gen.generate_files(_SAMPLE_REFERENCE, handwritten_source="")
    for relpath, content in files.items():
        line_count = content.count("\n") + 1
        assert line_count <= gen._FILE_LINE_CAP, f"{relpath}: {line_count}줄"


def test_generate_files_skips_fully_implemented_reference() -> None:
    handwritten = (
        'tr_id = "TTTC0011U"\ntr_id = "VTTC0011U"\ntr_id = "H0STCNT0"\ntr_id = "CTSC8407R"\n'
    )
    files = gen.generate_files(_SAMPLE_REFERENCE, handwritten)
    assert set(files) == {"_protocols.py", "__init__.py"}


# ---------------------------------------------------------------------------
# 실제 저장소 산출물 — 드리프트 없음 + 매트릭스 미착수 0
# ---------------------------------------------------------------------------

_COVERAGE_MODULE = _load_module("kis_tr_coverage", SCRIPTS_DIR / "kis_tr_coverage.py")


def test_committed_generated_dir_matches_fresh_regeneration() -> None:
    reference = _COVERAGE_MODULE.load_reference(gen.REFERENCE_PATH)
    handwritten_source = gen.scan_handwritten_source(gen.ADAPTER_DIR)
    expected = gen.generate_files(reference, handwritten_source)

    committed = {
        p.name: p.read_text(encoding="utf-8") for p in gen.GENERATED_DIR.glob("*.py")
    }

    assert committed == expected


def test_real_matrix_has_zero_not_started_after_generation() -> None:
    reference = _COVERAGE_MODULE.load_reference(gen.REFERENCE_PATH)
    adapter_source = _COVERAGE_MODULE.scan_adapter_source(gen.ADAPTER_DIR)  # generated/ 포함(재귀)

    matrix = _COVERAGE_MODULE.build_matrix(reference, adapter_source)

    not_started = sum(1 for row in matrix.rows if row.reason == "미착수")
    assert not_started == 0


def test_real_generated_files_all_within_line_cap() -> None:
    for path in gen.GENERATED_DIR.glob("*.py"):
        line_count = path.read_text(encoding="utf-8").count("\n") + 1
        assert line_count <= gen._FILE_LINE_CAP, f"{path.name}: {line_count}줄"
