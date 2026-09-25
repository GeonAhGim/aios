"""DEEPEN task-3048 — BT-14 vector_oss_eval_gate D2 증빙.

원 task-2181(BT-14 BACKTEST_VECTOR_EVAL.md, commit 1b53ad92)은 IND-9와 같은
계열의 순수 문서 산출물이라 기계 검증 가능한 회귀가 없었다(task-2727 DEPTH
감사가 IND-9를 D2 미달로 판정한 것과 동일 결함). 이 파일은 IND-9 deepen
(task-2919, `src/core/indicators/oss_eval_gate.py`)이 세운 선례를 그대로
따라 `src/foundation/backtest/domain/vector_oss_eval_gate.py`(§1 코드 차용
거부 후보·§4 numpy-only 결론을 실제 `pyproject.toml` 의존성과 대조하는 순수
게이트)에 ADR-2026-09-09-C D2 체크리스트를 채운다 — 새 백테스트 기능 없음,
깊이만 올린다(BT는 안전축 아님, D2 floor).

1. negative >= 3 — 빈 문서 · §1 후보 헤딩/판정 드리프트 · §4 결론 문구 변조
   · 거부 라이선스 패키지가 실제로 pyproject.toml에 선언된 경우 · §4 YAGNI로
   유보한 numba가 조기 도입된 경우.
2. 실패 주입 -- 문서/의존성 파일 읽기 IOError가 CLI exit 1로 표면화.
3. 성능 단언 -- 실제 EVAL.md + pyproject.toml 1,000회 게이트 실행이
   절대시간 예산 내.
4. 게이트 적색 재현 -- CLI `main()`이 변조 문서/의존성에서 exit 1, 원문은
   exit 0.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from scripts.check_backtest_vector_oss_eval import main as gate_main
from src.foundation.backtest.domain.vector_oss_eval_gate import (
    BANNED_CODE_BORROW_PACKAGES,
    PREMATURE_ADOPTION_PACKAGES,
    VectorOssEvalGateError,
    assert_vector_oss_eval_gate,
    extract_declared_dependencies,
)

_REPO_ROOT = Path(__file__).resolve().parents[4]
_EVAL_PATH = _REPO_ROOT / "docs" / "design" / "BACKTEST_VECTOR_EVAL.md"
_PYPROJECT_PATH = _REPO_ROOT / "pyproject.toml"


@pytest.fixture(scope="module")
def eval_text() -> str:
    return _EVAL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pyproject_text() -> str:
    return _PYPROJECT_PATH.read_text(encoding="utf-8")


def test_happy_path_accepts_canonical_document(eval_text: str, pyproject_text: str) -> None:
    report = assert_vector_oss_eval_gate(eval_text, pyproject_text)
    assert report.excluded_packages == frozenset(BANNED_CODE_BORROW_PACKAGES)
    assert report.numpy_declared is True
    assert report.numba_declared is False
    assert "numpy" in report.declared_dependencies


def test_extract_declared_dependencies_reads_real_pyproject(pyproject_text: str) -> None:
    names = extract_declared_dependencies(pyproject_text)
    assert "numpy" in names
    assert "fastapi" in names
    # None of the code-borrow-rejected or YAGNI-deferred candidates are declared.
    assert not names & frozenset(BANNED_CODE_BORROW_PACKAGES)
    assert not names & frozenset(PREMATURE_ADOPTION_PACKAGES)


def test_negative_empty_document_rejected(pyproject_text: str) -> None:
    with pytest.raises(VectorOssEvalGateError, match="EMPTY_DOCUMENT"):
        assert_vector_oss_eval_gate("   \n", pyproject_text)


def test_negative_candidate_verdict_dropped_from_section1(
    eval_text: str, pyproject_text: str
) -> None:
    # Simulate someone quietly flipping vectorbt's verdict from reject to
    # admit without updating the rest of the document -- must be caught.
    mutilated = eval_text.replace(
        "반입 가/부: 부(코드 차용 기준).", "반입 가/부: 가(코드 차용 기준).", 1
    )
    assert mutilated != eval_text
    with pytest.raises(VectorOssEvalGateError, match="VERDICT_DRIFT"):
        assert_vector_oss_eval_gate(mutilated, pyproject_text)


def test_negative_conclusion_heading_altered(eval_text: str, pyproject_text: str) -> None:
    # Flip the frozen §4 numpy-only conclusion without updating anything
    # else -- must be flagged as drift, not silently accepted.
    flipped = eval_text.replace(
        "**BT-15(`backtest/vector/{arrays,signals,fills}.py`)는 numpy 자체 구현으로 착수한다.**",
        "**BT-15는 vectorbt를 벤더링해 착수한다.**",
        1,
    )
    assert flipped != eval_text
    with pytest.raises(VectorOssEvalGateError, match="CONCLUSION_DRIFT"):
        assert_vector_oss_eval_gate(flipped, pyproject_text)


def test_negative_banned_code_borrow_package_declared_in_pyproject(
    eval_text: str, pyproject_text: str
) -> None:
    # This is the core machine-verifiable regression the leaf was missing:
    # if a future change adds a Commons-Clause package as a real dependency,
    # the gate must fail even though the document text itself is untouched.
    poisoned = pyproject_text.replace('"numpy>=2.0",', '"numpy>=2.0",\n    "vectorbt>=1.1",', 1)
    assert poisoned != pyproject_text
    with pytest.raises(VectorOssEvalGateError, match="BANNED_CODE_BORROW_DEPENDENCY"):
        assert_vector_oss_eval_gate(eval_text, poisoned)


def test_negative_numba_adopted_before_bt16_reevaluation(
    eval_text: str, pyproject_text: str
) -> None:
    # §4 explicitly defers numba to a BT-16 re-evaluation. If a future
    # change adds it preemptively, that decision was silently reversed.
    poisoned = pyproject_text.replace('"numpy>=2.0",', '"numpy>=2.0",\n    "numba>=0.61",', 1)
    assert poisoned != pyproject_text
    with pytest.raises(VectorOssEvalGateError, match="PREMATURE_ADOPTION_DEPENDENCY"):
        assert_vector_oss_eval_gate(eval_text, poisoned)


def test_failure_injection_read_oserror_surfaces_as_gate_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "no-such-eval.md"

    def _boom(self: Path, *args: object, **kwargs: object) -> str:
        raise OSError("injected disk failure")

    monkeypatch.setattr(Path, "read_text", _boom)
    assert gate_main([str(missing)]) == 1


@pytest.mark.perf
def test_perf_gate_under_budget(eval_text: str, pyproject_text: str) -> None:
    n = 1000
    budget_sec = 2.0
    assert_vector_oss_eval_gate(eval_text, pyproject_text)  # warm
    start = time.perf_counter()
    for _ in range(n):
        assert_vector_oss_eval_gate(eval_text, pyproject_text)
    elapsed = time.perf_counter() - start
    print(f"[BT-14 vector_oss_eval_gate] {n} runs {elapsed:.3f}s (budget<{budget_sec}s)")
    assert elapsed < budget_sec, (
        f"{n}회 게이트 실행이 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )


def test_gate_red_cli_rejects_mutated_document(eval_text: str, tmp_path: Path) -> None:
    mutated = eval_text.replace(
        "**BT-15는 numpy 자체 구현으로 착수 가능하다. BT-16/17은 BT-15 완료 후 배정한다.**",
        "**BT-15는 vectorbt로 착수한다.**",
        1,
    )
    doc_path = tmp_path / "mutated_eval.md"
    doc_path.write_text(mutated, encoding="utf-8")
    assert gate_main([str(doc_path), str(_PYPROJECT_PATH)]) == 1


def test_gate_red_cli_rejects_poisoned_pyproject(eval_text: str, tmp_path: Path) -> None:
    poisoned = _PYPROJECT_PATH.read_text(encoding="utf-8").replace(
        '"numpy>=2.0",', '"numpy>=2.0",\n    "lib-pybroker>=2.0",', 1
    )
    doc_path = tmp_path / "eval.md"
    pyproject_path = tmp_path / "pyproject.toml"
    doc_path.write_text(eval_text, encoding="utf-8")
    pyproject_path.write_text(poisoned, encoding="utf-8")
    assert gate_main([str(doc_path), str(pyproject_path)]) == 1


def test_gate_green_cli_accepts_canonical_inputs() -> None:
    assert gate_main([str(_EVAL_PATH), str(_PYPROJECT_PATH)]) == 0
