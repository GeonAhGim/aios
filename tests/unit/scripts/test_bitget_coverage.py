"""scripts/bitget_coverage.py 단위 테스트 — BR-9(ADR-2026-09-06-I D5).

DoD: (1) 커버리지 하락 시 FAIL, (2) 분류는 항상 3종 중 하나만 반환(침묵
누락 불가), (3) 멀티 경로 셀("`a`, `/b`")의 접미사 확장이 문서 관례대로
동작, (4) 템플릿 자리표시자(`{marginType}`)가 소스의 다른 변수명과도
매칭, (5) 같은 입력에 같은 바이트. 전부 합성 데이터(tmp_path)로 검증 —
네트워크 접근 없음.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

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


bitget_coverage = _load_module("bitget_coverage", SCRIPTS_DIR / "bitget_coverage.py")


def _write_doc(tmp_path: Path, body: str, name: str = "spec.md") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# parse_spec_doc — 행 추출 규칙
# ---------------------------------------------------------------------------


def test_parse_spec_doc_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(bitget_coverage.BitgetCoverageError):
        bitget_coverage.parse_spec_doc(tmp_path / "does-not-exist.md")


def test_parse_spec_doc_extracts_simple_row(tmp_path: Path) -> None:
    doc = _write_doc(
        tmp_path,
        "### 1.1 Spot\n\n"
        "| 함수 목적 | Method | Path | 우선순위 |\n"
        "|---|---|---|---|\n"
        "| 현재가 | GET | `/api/v2/spot/market/tickers` | P0 |\n",
    )
    rows = bitget_coverage.parse_spec_doc(doc)
    assert len(rows) == 1
    assert rows[0].method == "GET"
    assert rows[0].path == "/api/v2/spot/market/tickers"
    assert rows[0].category == "1.1 Spot"
    assert rows[0].priority == "P0"


def test_parse_spec_doc_expands_suffix_from_previous_path(tmp_path: Path) -> None:
    """"`/api/v2/mix/market/ticker`, `/tickers`" -> 두 번째는 첫 경로의
    마지막 세그먼트를 대체한 접미사(문서 관례, 02b §5.1)."""
    doc = _write_doc(
        tmp_path,
        "### 5.1 Market\n\n"
        "| 함수 목적 | Method | Path | 우선순위 |\n"
        "|---|---|---|---|\n"
        "| 현재가 | GET | `/api/v2/mix/market/ticker`, `/tickers` | P0 |\n",
    )
    rows = bitget_coverage.parse_spec_doc(doc)
    assert [r.path for r in rows] == [
        "/api/v2/mix/market/ticker",
        "/api/v2/mix/market/tickers",
    ]


def test_parse_spec_doc_skips_separator_and_non_table_lines(tmp_path: Path) -> None:
    doc = _write_doc(
        tmp_path,
        "# 제목\n\n산문 한 줄.\n\n"
        "| 함수 목적 | Method | Path |\n"
        "|---|---|---|\n"
        "| 캔들 | GET | `/api/v2/spot/market/candles` |\n",
    )
    rows = bitget_coverage.parse_spec_doc(doc)
    assert len(rows) == 1


def test_parse_spec_doc_forbidden_row_captures_priority(tmp_path: Path) -> None:
    doc = _write_doc(
        tmp_path,
        "### 3.3 Account\n\n"
        "| 함수 목적 | Method | Path | 우선순위 |\n"
        "|---|---|---|---|\n"
        "| 출금 신청 | POST | `/api/v2/spot/wallet/withdrawal` | **금지** |\n",
    )
    rows = bitget_coverage.parse_spec_doc(doc)
    assert rows[0].priority == "금지"


# ---------------------------------------------------------------------------
# load_reference — 다중 문서 병합, 빈 결과 fail-closed
# ---------------------------------------------------------------------------


def test_load_reference_empty_raises(tmp_path: Path) -> None:
    doc = _write_doc(tmp_path, "# 제목\n\n산문만 있고 표 없음\n")
    with pytest.raises(bitget_coverage.BitgetCoverageError):
        bitget_coverage.load_reference((doc,))


def test_load_reference_dedupes_across_docs_keeping_first(tmp_path: Path) -> None:
    doc1 = _write_doc(
        tmp_path,
        "### A\n\n| 함수 목적 | Method | Path |\n|---|---|---|\n"
        "| x | GET | `/api/v2/dup` |\n",
        name="doc1.md",
    )
    doc2 = _write_doc(
        tmp_path,
        "### B\n\n| 함수 목적 | Method | Path |\n|---|---|---|\n"
        "| y | GET | `/api/v2/dup` |\n",
        name="doc2.md",
    )
    rows = bitget_coverage.load_reference((doc1, doc2))
    assert len(rows) == 1
    assert rows[0].category == "A"


# ---------------------------------------------------------------------------
# _path_implemented — 리터럴 + 템플릿 자리표시자 대조
# ---------------------------------------------------------------------------


def test_path_implemented_literal_match() -> None:
    source = 'await self._request("GET", "/api/v2/spot/market/tickers")\n'
    assert bitget_coverage._path_implemented("/api/v2/spot/market/tickers", source) is True


def test_path_implemented_literal_absent() -> None:
    source = 'await self._request("GET", "/api/v2/spot/public/coins")\n'
    assert bitget_coverage._path_implemented("/api/v2/spot/market/tickers", source) is False


def test_path_implemented_template_matches_different_variable_name() -> None:
    """기준 문서는 `{marginType}`, 실제 소스는 `{margin_type}` — 변수명이
    달라도 중괄호 자리표시자로 대조해야 매칭된다."""
    source = 'f"/api/v2/margin/{margin_type}/place-order"\n'
    assert (
        bitget_coverage._path_implemented("/api/v2/margin/{marginType}/place-order", source)
        is True
    )


def test_path_implemented_template_rejects_wrong_literal_segment() -> None:
    source = 'f"/api/v2/margin/{margin_type}/cancel-order"\n'
    assert (
        bitget_coverage._path_implemented("/api/v2/margin/{marginType}/place-order", source)
        is False
    )


# ---------------------------------------------------------------------------
# classify() — 사유는 항상 3종 중 하나 (D5)
# ---------------------------------------------------------------------------


def _row(priority: str = "P1") -> bitget_coverage.EndpointRow:
    return bitget_coverage.EndpointRow(
        method="GET",
        path="/api/v2/spot/market/tickers",
        label="현재가",
        category="Market",
        priority=priority,
        source_doc="spec.md",
    )


def test_classify_implemented_wins_regardless_of_priority() -> None:
    assert bitget_coverage.classify(_row(priority="금지"), implemented=True) == "구현됨"


def test_classify_forbidden_priority_is_out_of_scope() -> None:
    assert bitget_coverage.classify(_row(priority="금지"), implemented=False) == "범위밖"


def test_classify_low_priority_defaults_to_not_started() -> None:
    assert bitget_coverage.classify(_row(priority="P2"), implemented=False) == "미착수"


# ---------------------------------------------------------------------------
# build_matrix — 카운트·정렬·불변조건
# ---------------------------------------------------------------------------


def test_build_matrix_counts_and_sorts_by_path(tmp_path: Path) -> None:
    doc = _write_doc(
        tmp_path,
        "### A\n\n| 함수 목적 | Method | Path | 우선순위 |\n|---|---|---|---|\n"
        "| b | GET | `/api/v2/b` | P1 |\n"
        "| a | GET | `/api/v2/a` | P1 |\n"
        "| w | POST | `/api/v2/withdraw` | **금지** |\n",
    )
    reference = bitget_coverage.load_reference((doc,))
    matrix = bitget_coverage.build_matrix(reference, adapter_source='"/api/v2/a"\n')

    assert matrix.total == 3
    assert matrix.implemented == 1
    assert [r.path for r in matrix.rows] == ["/api/v2/a", "/api/v2/b", "/api/v2/withdraw"]
    reasons = {r.path: r.reason for r in matrix.rows}
    assert reasons["/api/v2/a"] == "구현됨"
    assert reasons["/api/v2/b"] == "미착수"
    assert reasons["/api/v2/withdraw"] == "범위밖"


def test_build_matrix_percent_rounds_to_two_decimals(tmp_path: Path) -> None:
    doc = _write_doc(
        tmp_path,
        "### A\n\n| 함수 목적 | Method | Path |\n|---|---|---|\n"
        "| a | GET | `/api/v2/a` |\n"
        "| b | GET | `/api/v2/b` |\n"
        "| c | GET | `/api/v2/c` |\n",
    )
    reference = bitget_coverage.load_reference((doc,))
    matrix = bitget_coverage.build_matrix(reference, adapter_source='"/api/v2/a"\n')
    assert matrix.percent == round(1 / 3 * 100, 2)


# ---------------------------------------------------------------------------
# render_* — 같은 입력 -> 바이트 동일
# ---------------------------------------------------------------------------


def test_render_markdown_is_byte_identical_for_same_input(tmp_path: Path) -> None:
    doc = _write_doc(
        tmp_path,
        "### A\n\n| 함수 목적 | Method | Path |\n|---|---|---|\n"
        "| a | GET | `/api/v2/a` |\n",
    )
    reference = bitget_coverage.load_reference((doc,))
    matrix = bitget_coverage.build_matrix(reference, adapter_source="")

    first = bitget_coverage.render_markdown(matrix)
    second = bitget_coverage.render_markdown(matrix)
    assert first == second
    assert "미착수" in first


def test_render_coverage_txt_is_byte_identical_for_same_input(tmp_path: Path) -> None:
    doc = _write_doc(
        tmp_path,
        "### A\n\n| 함수 목적 | Method | Path |\n|---|---|---|\n"
        "| a | GET | `/api/v2/a` |\n",
    )
    reference = bitget_coverage.load_reference((doc,))
    matrix = bitget_coverage.build_matrix(reference, adapter_source="")
    assert bitget_coverage.render_coverage_txt(matrix) == bitget_coverage.render_coverage_txt(
        matrix
    )


# ---------------------------------------------------------------------------
# main() — 래칫 DoD
# ---------------------------------------------------------------------------


def _run_main(tmp_path: Path, spec_doc: Path, adapter_dir: Path, coverage_txt: Path) -> int:
    return bitget_coverage.main(
        [
            "--spec-docs",
            str(spec_doc),
            "--adapter-dir",
            str(adapter_dir),
            "--matrix-md",
            str(tmp_path / "MATRIX.md"),
            "--coverage-txt",
            str(coverage_txt),
        ]
    )


_SAMPLE_DOC_BODY = (
    "### A\n\n| 함수 목적 | Method | Path |\n|---|---|---|\n"
    "| a | GET | `/api/v2/a` |\n"
    "| b | GET | `/api/v2/b` |\n"
    "| c | GET | `/api/v2/c` |\n"
)


def test_first_run_initializes_baseline(tmp_path: Path) -> None:
    doc = _write_doc(tmp_path, _SAMPLE_DOC_BODY)
    adapter_dir = tmp_path / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "a.py").write_text('"/api/v2/a"\n', encoding="utf-8")
    coverage_txt = tmp_path / "bitget-coverage.txt"

    exit_code = _run_main(tmp_path, doc, adapter_dir, coverage_txt)

    assert exit_code == 0
    assert coverage_txt.read_text(encoding="utf-8").strip() == f"{round(1 / 3 * 100, 2):.2f}"
    assert (tmp_path / "MATRIX.md").exists()


def test_lowered_coverage_fails(tmp_path: Path) -> None:
    doc = _write_doc(tmp_path, _SAMPLE_DOC_BODY)
    adapter_dir = tmp_path / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "a.py").write_text("# 아무 것도 구현 안 함\n", encoding="utf-8")
    coverage_txt = tmp_path / "bitget-coverage.txt"
    coverage_txt.write_text("50.00\n", encoding="utf-8")

    exit_code = _run_main(tmp_path, doc, adapter_dir, coverage_txt)

    assert exit_code == 1
    assert coverage_txt.read_text(encoding="utf-8").strip() == "50.00"


def test_risen_coverage_ratchets_baseline_up(tmp_path: Path) -> None:
    doc = _write_doc(tmp_path, _SAMPLE_DOC_BODY)
    adapter_dir = tmp_path / "adapter"
    adapter_dir.mkdir()
    (adapter_dir / "a.py").write_text('"/api/v2/a"\n"/api/v2/b"\n', encoding="utf-8")
    coverage_txt = tmp_path / "bitget-coverage.txt"
    coverage_txt.write_text("0.00\n", encoding="utf-8")

    exit_code = _run_main(tmp_path, doc, adapter_dir, coverage_txt)

    assert exit_code == 0
    assert coverage_txt.read_text(encoding="utf-8").strip() == f"{round(2 / 3 * 100, 2):.2f}"


def test_main_missing_spec_doc_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    adapter_dir = tmp_path / "adapter"
    adapter_dir.mkdir()

    exit_code = _run_main(tmp_path, tmp_path / "missing.md", adapter_dir, tmp_path / "cov.txt")

    assert exit_code == 1
    assert "FAIL" in capsys.readouterr().out
