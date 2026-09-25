"""upbit_openapi_coverage.py 오프라인 픽스처 테스트 -- BR-21(task-7147).

네트워크 없이 build_matrix/render_markdown/classify/scan_adapter_source의
동작을 확인한다. 실제 파일 I/O는 tmp_path 픽스처로 격리한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

from upbit_openapi_coverage import (  # noqa: E402
    UpbitCoverageError,
    build_matrix,
    classify,
    load_reference,
    main,
    read_baseline_percent,
    render_coverage_txt,
    render_markdown,
    scan_adapter_source,
)

_REFERENCE_FIXTURE = {
    "source_url": "https://docs.upbit.com/kr/reference",
    "extraction_method": "live-verified against api.upbit.com",
    "fetched_at": "2026-09-25T00:00:00+00:00",
    "spec_version": "unversioned",
    "spec_title": "Upbit Open API",
    "endpoint_count": 2,
    "endpoints": [
        {"path": "/v1/market/all", "method": "GET", "summary": "마켓 코드 조회"},
        {"path": "/v1/orders", "method": "POST", "summary": "주문하기"},
    ],
}


def test_scan_adapter_source_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert scan_adapter_source(tmp_path / "does-not-exist") == ""


def test_scan_adapter_source_reads_py_files(tmp_path: Path) -> None:
    adapter_dir = tmp_path / "upbit"
    adapter_dir.mkdir()
    (adapter_dir / "adapter.py").write_text('PATH = "/v1/market/all"\n', encoding="utf-8")

    source = scan_adapter_source(adapter_dir)

    assert "/v1/market/all" in source


def test_classify_defaults_to_not_started() -> None:
    assert classify("/v1/orders", "POST", implemented=False) == "미착수"


def test_classify_implemented() -> None:
    assert classify("/v1/orders", "POST", implemented=True) == "구현됨"


def test_build_matrix_all_not_started_with_no_adapter() -> None:
    matrix = build_matrix(_REFERENCE_FIXTURE, adapter_source="")

    assert matrix.total == 2
    assert matrix.implemented == 0
    assert matrix.percent == 0.0
    assert all(row.reason == "미착수" for row in matrix.rows)


def test_build_matrix_detects_implemented_endpoint() -> None:
    matrix = build_matrix(_REFERENCE_FIXTURE, adapter_source='ORDER_PATH = "/v1/orders"\n')

    reasons = {row.path: row.reason for row in matrix.rows}
    assert reasons["/v1/orders"] == "구현됨"
    assert reasons["/v1/market/all"] == "미착수"
    assert matrix.implemented == 1
    assert matrix.percent == 50.0


def test_build_matrix_rejects_invalid_reason(monkeypatch) -> None:
    import upbit_openapi_coverage as module

    monkeypatch.setattr(module, "classify", lambda *a, **kw: "존재하지않는사유")

    try:
        build_matrix(_REFERENCE_FIXTURE, adapter_source="")
    except UpbitCoverageError as exc:
        assert "허용되지 않은 사유" in str(exc)
    else:
        raise AssertionError("UpbitCoverageError를 기대했지만 발생하지 않음")


def test_render_markdown_reports_zero_percent_when_nothing_implemented() -> None:
    matrix = build_matrix(_REFERENCE_FIXTURE, adapter_source="")

    markdown = render_markdown(_REFERENCE_FIXTURE, matrix)

    assert "| 0 | 0 | 0 | 2 | 2 | 0.00% |" in markdown
    assert "`/v1/market/all`" in markdown
    assert "미착수" in markdown


def test_render_coverage_txt_format() -> None:
    matrix = build_matrix(_REFERENCE_FIXTURE, adapter_source="")

    assert render_coverage_txt(matrix) == "0.00\n"


def test_load_reference_missing_file_raises(tmp_path: Path) -> None:
    try:
        load_reference(tmp_path / "missing.json")
    except UpbitCoverageError as exc:
        assert "기준 목록 없음" in str(exc)
    else:
        raise AssertionError("UpbitCoverageError를 기대했지만 발생하지 않음")


def test_load_reference_empty_endpoints_raises(tmp_path: Path) -> None:
    import json

    ref_path = tmp_path / "reference.json"
    ref_path.write_text(json.dumps({"endpoints": []}), encoding="utf-8")

    try:
        load_reference(ref_path)
    except UpbitCoverageError as exc:
        assert "비어있거나" in str(exc)
    else:
        raise AssertionError("UpbitCoverageError를 기대했지만 발생하지 않음")


def test_read_baseline_percent_missing_file_returns_none(tmp_path: Path) -> None:
    assert read_baseline_percent(tmp_path / "missing.txt") is None


def test_read_baseline_percent_non_numeric_raises(tmp_path: Path) -> None:
    path = tmp_path / "baseline.txt"
    path.write_text("not-a-number", encoding="utf-8")

    try:
        read_baseline_percent(path)
    except UpbitCoverageError as exc:
        assert "숫자가 아님" in str(exc)
    else:
        raise AssertionError("UpbitCoverageError를 기대했지만 발생하지 않음")


def test_main_initializes_baseline_when_missing(tmp_path: Path) -> None:
    import json

    reference_path = tmp_path / "reference.json"
    reference_path.write_text(json.dumps(_REFERENCE_FIXTURE), encoding="utf-8")
    matrix_md = tmp_path / "MATRIX.md"
    coverage_txt = tmp_path / "coverage.txt"
    adapter_dir = tmp_path / "no-adapter"

    exit_code = main(
        [
            "--reference",
            str(reference_path),
            "--adapter-dir",
            str(adapter_dir),
            "--matrix-md",
            str(matrix_md),
            "--coverage-txt",
            str(coverage_txt),
        ]
    )

    assert exit_code == 0
    assert coverage_txt.read_text(encoding="utf-8") == "0.00\n"
    assert "미착수" in matrix_md.read_text(encoding="utf-8")


def test_main_fails_on_coverage_drop(tmp_path: Path) -> None:
    import json

    reference_path = tmp_path / "reference.json"
    reference_path.write_text(json.dumps(_REFERENCE_FIXTURE), encoding="utf-8")
    matrix_md = tmp_path / "MATRIX.md"
    coverage_txt = tmp_path / "coverage.txt"
    coverage_txt.write_text("50.00\n", encoding="utf-8")
    adapter_dir = tmp_path / "no-adapter"

    exit_code = main(
        [
            "--reference",
            str(reference_path),
            "--adapter-dir",
            str(adapter_dir),
            "--matrix-md",
            str(matrix_md),
            "--coverage-txt",
            str(coverage_txt),
        ]
    )

    assert exit_code == 1
    assert coverage_txt.read_text(encoding="utf-8") == "50.00\n"
