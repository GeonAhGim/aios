"""DEEPEN task-2904 — RD-1 source_eval_gate D2 증빙.

DEPTH_DC_RD(task-2726)가 task-2401을 D0로 판정했다(문서 1건·코드/테스트
전무). 이 파일은 `domain/source_eval_gate.py`에 대해 ADR-2026-09-09-C D2
체크리스트를 채운다 — 새 리서치 어댑터 기능 없음, 깊이만 올린다.

1. negative ≥3 — 빈 문서·소스 누락·미확인 rate limit을 허용으로 위장·
   §9 표/본문 불일치·인용(blockquote/URL/날짜) 누락.
2. 실패 주입 — 읽기 IOError·본문 절단(구조 파괴)·허용 소스를 금지 표에만
   남기는 드리프트.
3. 성능 단언 — 실제 EVAL.md 1,000회 파싱이 절대시간 예산 내.
4. 게이트 적색 재현 — CLI `main()`이 변조 문서에서 exit 1, 원문은 exit 0.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from scripts.check_research_data_source_eval import main as gate_main
from src.foundation.research_data.domain.source_eval_gate import (
    EXPECTED_ADMISSION,
    LAYER_A_SOURCE_IDS,
    SourceEvalGateError,
    assert_source_eval_gate,
    parse_source_eval_markdown,
)

_REPO_ROOT = Path(__file__).resolve().parents[4]
_EVAL_PATH = _REPO_ROOT / "docs" / "design" / "RESEARCH_DATA_SOURCE_EVAL.md"


@pytest.fixture(scope="module")
def eval_text() -> str:
    return _EVAL_PATH.read_text(encoding="utf-8")


def test_happy_path_parses_eight_layer_a_sources(eval_text: str) -> None:
    report = assert_source_eval_gate(eval_text)
    assert [r.source_id for r in report.records] == list(LAYER_A_SOURCE_IDS)
    for record in report.records:
        assert record.admission == EXPECTED_ADMISSION[record.source_id]
        assert record.has_blockquote and record.has_url and record.has_check_date


def test_negative_empty_document_rejected() -> None:
    with pytest.raises(SourceEvalGateError, match="EMPTY_DOCUMENT"):
        parse_source_eval_markdown("   \n")


def test_negative_missing_source_section_rejected(eval_text: str) -> None:
    mutilated = eval_text.replace("## 2. ECOS", "## 2. UNKNOWN_VENDOR")
    with pytest.raises(SourceEvalGateError, match="UNKNOWN_SECTION|MISSING_SOURCES"):
        assert_source_eval_gate(mutilated)


def test_negative_unconfirmed_rate_limit_cannot_be_allowed(eval_text: str) -> None:
    # Flip ECOS conclusion to 허용 while leaving rate limit 미확인 — DoD (c).
    flipped = eval_text.replace(
        "**결론: 반입 금지** (rate limit 미확인).",
        "**결론: 허용** (rate limit 미확인 — 부정 실험).",
        1,
    )
    with pytest.raises(SourceEvalGateError) as excinfo:
        assert_source_eval_gate(flipped)
    assert excinfo.value.code in {
        "UNCONFIRMED_RATE_LIMIT_ALLOWED",
        "ADMISSION_DRIFT",
        "TABLE_SECTION_MISMATCH",
    }


def test_negative_missing_blockquote_citation(eval_text: str) -> None:
    # Strip OpenDART blockquotes only (lines starting with '>' inside §1).
    lines = eval_text.splitlines(keepends=True)
    out: list[str] = []
    in_opendart = False
    for line in lines:
        if line.startswith("## 1. OpenDART"):
            in_opendart = True
        elif line.startswith("## 2."):
            in_opendart = False
        if in_opendart and line.startswith(">"):
            continue
        out.append(line)
    with pytest.raises(SourceEvalGateError, match="MISSING_QUOTE"):
        assert_source_eval_gate("".join(out))


def test_negative_allow_deny_table_overlap(eval_text: str) -> None:
    # Duplicate OpenDART into the deny table rows.
    poisoned = eval_text.replace(
        "| ECOS | rate limit **미확인**",
        "| OpenDART | synthetic overlap |\n| ECOS | rate limit **미확인**",
        1,
    )
    with pytest.raises(SourceEvalGateError, match="TABLE_OVERLAP|TABLE_COVERAGE"):
        assert_source_eval_gate(poisoned)


def test_failure_injection_truncated_markdown_raises(eval_text: str) -> None:
    truncated = eval_text[: eval_text.index("## 5. FRED")]
    with pytest.raises(SourceEvalGateError, match="MISSING_SOURCES"):
        assert_source_eval_gate(truncated)


def test_failure_injection_read_oserror_surfaces_as_gate_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "no-such-eval.md"

    def _boom(self: Path, *args: object, **kwargs: object) -> str:
        raise OSError("injected disk failure")

    monkeypatch.setattr(Path, "read_text", _boom)
    assert gate_main([str(missing)]) == 1


def test_failure_injection_section_table_drift(eval_text: str) -> None:
    # Keep §2 conclusion as deny but move ECOS into the allow table only.
    opendart_row = (
        "| OpenDART | 구조화 필드 `store_full`, 서술 본문 `store_excerpt` "
        "| 키 필수, 일 20,000건 초과 시 오류 |\n"
    )
    drifted = eval_text.replace(
        opendart_row,
        opendart_row + "| ECOS | synthetic allow-table row | should conflict |\n",
        1,
    ).replace(
        "| ECOS | rate limit **미확인**",
        "| ECOS_REMOVED | rate limit **미확인**",
        1,
    )
    with pytest.raises(SourceEvalGateError) as excinfo:
        assert_source_eval_gate(drifted)
    assert excinfo.value.code in {
        "TABLE_OVERLAP",
        "TABLE_COVERAGE",
        "TABLE_SECTION_MISMATCH",
    }


@pytest.mark.perf
def test_perf_parse_eval_markdown_under_budget(eval_text: str) -> None:
    n = 1000
    budget_sec = 2.0
    # Warm once so first-parse import noise is outside the window.
    assert_source_eval_gate(eval_text)
    start = time.perf_counter()
    for _ in range(n):
        assert_source_eval_gate(eval_text)
    elapsed = time.perf_counter() - start
    print(f"[RD-1 source_eval_gate] {n} parses {elapsed:.3f}s (budget<{budget_sec}s)")
    assert elapsed < budget_sec, (
        f"{n}회 파싱이 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )


def test_gate_red_cli_rejects_mutated_document(
    eval_text: str, tmp_path: Path
) -> None:
    # Drop the entire OpenDART numbered section so LAYER_A coverage fails.
    start = eval_text.index("## 1. OpenDART")
    end = eval_text.index("## 2. ECOS")
    mutated = eval_text[:start] + eval_text[end:]
    path = tmp_path / "mutated_eval.md"
    path.write_text(mutated, encoding="utf-8")
    assert gate_main([str(path)]) == 1


def test_gate_green_cli_accepts_canonical_document() -> None:
    assert gate_main([str(_EVAL_PATH)]) == 0
