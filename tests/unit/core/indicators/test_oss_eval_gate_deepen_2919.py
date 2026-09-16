"""DEEPEN task-2919 — IND-9 oss_eval_gate D2 증빙.

task-2727 DEPTH 감사(docs/audit/DEPTH_DSL_IND.md)가 원 task-1536(IND-9
INDICATOR_OSS_EVAL.md, commit da52f3c)을 D2 미달로 판정했다 — 순수 문서
산출물이라 기계 검증 가능한 회귀가 없었다. 이 파일은
`src/core/indicators/oss_eval_gate.py`(§5 GPL/LGPL 배제·§6 채점표 결론을
실제 `pyproject.toml` 의존성과 대조하는 순수 게이트)에 ADR-2026-09-09-C D2
체크리스트를 채운다 — 새 지표/브리지 기능 없음, 깊이만 올린다(DSL/IND는
안전축 아님, D2 floor).

1. negative >= 3 — 빈 문서 · §5 배제 후보 누락(드리프트) · §6 결론 문구
   변조(드리프트) · GPL/LGPL 패키지가 실제로 pyproject.toml에 선언된 경우.
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

from scripts.check_indicator_oss_eval import main as gate_main
from src.core.indicators.oss_eval_gate import (
    BANNED_LICENSE_PACKAGES,
    OssEvalGateError,
    assert_oss_eval_gate,
    extract_declared_dependencies,
)

_REPO_ROOT = Path(__file__).resolve().parents[4]
_EVAL_PATH = _REPO_ROOT / "docs" / "design" / "INDICATOR_OSS_EVAL.md"
_PYPROJECT_PATH = _REPO_ROOT / "pyproject.toml"


@pytest.fixture(scope="module")
def eval_text() -> str:
    return _EVAL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pyproject_text() -> str:
    return _PYPROJECT_PATH.read_text(encoding="utf-8")


def test_happy_path_accepts_canonical_document(eval_text: str, pyproject_text: str) -> None:
    report = assert_oss_eval_gate(eval_text, pyproject_text)
    assert report.excluded_packages == frozenset(BANNED_LICENSE_PACKAGES)
    assert report.talib_declared is True
    assert "ta-lib" in report.declared_dependencies


def test_extract_declared_dependencies_reads_real_pyproject(
    pyproject_text: str,
) -> None:
    names = extract_declared_dependencies(pyproject_text)
    assert "fastapi" in names
    assert "ta-lib" in names
    # None of the GPL/LGPL candidates the document excludes are declared.
    assert not names & frozenset(BANNED_LICENSE_PACKAGES)


def test_negative_empty_document_rejected(pyproject_text: str) -> None:
    with pytest.raises(OssEvalGateError, match="EMPTY_DOCUMENT"):
        assert_oss_eval_gate("   \n", pyproject_text)


def test_negative_excluded_candidate_dropped_from_section5(
    eval_text: str, pyproject_text: str
) -> None:
    # Simulate someone quietly dropping backtrader from the §5 exclusion
    # table -- the gate must catch the drift, not silently accept it.
    section5_start = eval_text.find("## 5. GPL/LGPL")
    section6_start = eval_text.find("## 6. 채점표")
    mutilated = (
        eval_text[:section5_start]
        + eval_text[section5_start:section6_start].replace("backtrader", "XXXXXXXXXX")
        + eval_text[section6_start:]
    )
    with pytest.raises(OssEvalGateError, match="MISSING_EXCLUDED_CANDIDATE"):
        assert_oss_eval_gate(mutilated, pyproject_text)


def test_negative_conclusion_heading_altered(eval_text: str, pyproject_text: str) -> None:
    # Flip the frozen IND-10 conclusion from admit to reject without
    # updating the rest of the document -- must be flagged as drift.
    flipped = eval_text.replace(
        "### IND-10 (TA-Lib 브리지): **반입 가**",
        "### IND-10 (TA-Lib 브리지): **반입 불가**",
        1,
    )
    with pytest.raises(OssEvalGateError, match="CONCLUSION_DRIFT"):
        assert_oss_eval_gate(flipped, pyproject_text)


def test_negative_banned_license_package_declared_in_pyproject(
    eval_text: str, pyproject_text: str
) -> None:
    # This is the core machine-verifiable regression the leaf was missing:
    # if a future change adds a GPL/LGPL package as a real dependency, the
    # gate must fail even though the document text itself is untouched.
    poisoned = pyproject_text.replace(
        '"TA-Lib==0.7.1",', '"TA-Lib==0.7.1",\n    "backtrader>=1.9",', 1
    )
    assert poisoned != pyproject_text
    with pytest.raises(OssEvalGateError, match="BANNED_LICENSE_DEPENDENCY"):
        assert_oss_eval_gate(eval_text, poisoned)


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
    assert_oss_eval_gate(eval_text, pyproject_text)  # warm
    start = time.perf_counter()
    for _ in range(n):
        assert_oss_eval_gate(eval_text, pyproject_text)
    elapsed = time.perf_counter() - start
    print(f"[IND-9 oss_eval_gate] {n} runs {elapsed:.3f}s (budget<{budget_sec}s)")
    assert elapsed < budget_sec, (
        f"{n}회 게이트 실행이 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )


def test_gate_red_cli_rejects_mutated_document(eval_text: str, tmp_path: Path) -> None:
    mutated = eval_text.replace(
        "GPL/LGPL 3종은 어떤 형태로도 코드가 저장소에 들어오지 않는다.",
        "GPL/LGPL 3종은 상황에 따라 반입할 수 있다.",
        1,
    )
    doc_path = tmp_path / "mutated_eval.md"
    doc_path.write_text(mutated, encoding="utf-8")
    assert gate_main([str(doc_path), str(_PYPROJECT_PATH)]) == 1


def test_gate_red_cli_rejects_poisoned_pyproject(eval_text: str, tmp_path: Path) -> None:
    poisoned = _PYPROJECT_PATH.read_text(encoding="utf-8").replace(
        '"TA-Lib==0.7.1",', '"TA-Lib==0.7.1",\n    "nautilus_trader>=1.231",', 1
    )
    doc_path = tmp_path / "eval.md"
    pyproject_path = tmp_path / "pyproject.toml"
    doc_path.write_text(eval_text, encoding="utf-8")
    pyproject_path.write_text(poisoned, encoding="utf-8")
    assert gate_main([str(doc_path), str(pyproject_path)]) == 1


def test_gate_green_cli_accepts_canonical_inputs() -> None:
    assert gate_main([str(_EVAL_PATH), str(_PYPROJECT_PATH)]) == 0
