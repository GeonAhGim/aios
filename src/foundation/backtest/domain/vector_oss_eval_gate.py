"""BT-14 deepen (task-3048) — BACKTEST_VECTOR_EVAL.md DoD gate (pure).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 BT-14.
ADR-2026-09-05-A D1 + ADR-2026-09-06-G §4 (badge-trap correction: "Apache-2.0 +
Commons Clause" is an explicit reject grade, naming vectorbt/pybroker). The
scorecard itself (docs/design/BACKTEST_VECTOR_EVAL.md) is a pure document with
no code to exercise, so the machine-verifiable regression this gate adds is a
cross-check between the document's §1/§4 conclusions and the *actual* declared
dependencies in `pyproject.toml`: if a future change adds vectorbt, vectorbtpro,
or pybroker (a rejected code-borrowing verdict in the document) as a project
dependency, or adds numba before the YAGNI re-evaluation §4 reserves for BT-16,
this gate turns red. No I/O — callers pass in file text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "BANNED_CODE_BORROW_PACKAGES",
    "PREMATURE_ADOPTION_PACKAGES",
    "VectorOssEvalGateError",
    "VectorOssEvalReport",
    "extract_declared_dependencies",
    "assert_vector_oss_eval_gate",
]

# §1 candidates whose code-borrowing verdict is reject -- normalized pip
# distribution name -> license family. Frozen: changing membership here is a
# licensing decision, not a routine edit, and must be paired with a matching
# doc update.
BANNED_CODE_BORROW_PACKAGES: dict[str, str] = {
    "vectorbt": "Apache-2.0 + Commons Clause",
    "vectorbtpro": "Proprietary source-available",
    "lib-pybroker": "Apache-2.0 + Commons Clause",
    "pybroker": "Apache-2.0 + Commons Clause",
}

# §4 decision: numba is not adopted this leaf (YAGNI -- re-evaluate only after
# BT-16 measures numpy-only throughput against its budget). Not a license
# ban, a premature-adoption guard tied to this document's own conclusion.
PREMATURE_ADOPTION_PACKAGES: dict[str, str] = {
    "numba": "§4 YAGNI -- re-evaluate at BT-16, not preemptively here",
}

# (name token in §1 prose, verdict token that must co-occur in the same block)
_SECTION1_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("### 1.1 vectorbt", "반입 가/부: 부"),
    ("### 1.2 vectorbtpro", "반입 가/부: 부"),
    ("### 1.3 zipline-reloaded", "반입 가/부: 가"),
    ("### 1.4 pybroker", "반입 가/부: 부"),
)

_SECTION4_HEADING = "## 4. BT-15 결론 — 실제로 쓸 스택"
_SECTION4_CONCLUSION = (
    "**BT-15(`backtest/vector/{arrays,signals,fills}.py`)는 numpy 자체 구현으로 착수한다.**"
)
_SECTION4_NUMBA_DECISION = "`numba`는 이번 리프의 의존성에 추가하지 않는다"

_GATE_CLOSING_SENTENCE = (
    "**BT-15는 numpy 자체 구현으로 착수 가능하다. BT-16/17은 BT-15 완료 후 배정한다.**"
)


class VectorOssEvalGateError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class VectorOssEvalReport:
    excluded_packages: frozenset[str]
    declared_dependencies: frozenset[str]
    numpy_declared: bool
    numba_declared: bool


def _normalize_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def extract_declared_dependencies(pyproject_toml_text: str) -> frozenset[str]:
    """Pull PEP 508 requirement names out of `[project]` (main + optional-
    dependencies). Scoped to the `[project]` table text only, up to the next
    `[tool.` table, so unrelated quoted strings elsewhere (ruff/mypy config)
    are never mistaken for a dependency name.
    """
    match = re.search(r"\[project\](.*?)(?=\n\[tool\.|\Z)", pyproject_toml_text, re.S)
    if not match:
        raise VectorOssEvalGateError(
            "MISSING_PROJECT_TABLE", "pyproject.toml has no [project] table"
        )
    block = match.group(1)
    names: set[str] = set()
    for requirement in re.findall(r'"([^"]+)"', block):
        name_match = re.match(r"^\s*([A-Za-z][A-Za-z0-9._-]*)", requirement)
        if name_match:
            names.add(_normalize_name(name_match.group(1)))
    if not names:
        raise VectorOssEvalGateError(
            "EMPTY_DEPENDENCY_LIST", "[project] table yielded no dependency names"
        )
    return frozenset(names)


def assert_vector_oss_eval_gate(
    eval_markdown: str, pyproject_toml_text: str
) -> VectorOssEvalReport:
    """Full BT-14 DoD gate. Returns the report on success; raises on violation."""
    if not eval_markdown.strip():
        raise VectorOssEvalGateError("EMPTY_DOCUMENT", "BACKTEST_VECTOR_EVAL.md is empty")

    section1_start = eval_markdown.find("## 1. 라이선스 원문 확인")
    section2_start = eval_markdown.find("## 2. 설계 참조")
    if section1_start == -1 or section2_start == -1 or section2_start < section1_start:
        raise VectorOssEvalGateError(
            "MISSING_SECTION",
            "expected '## 1. 라이선스 원문 확인 ...' followed by '## 2. 설계 참조 ...'",
        )
    section1_text = eval_markdown[section1_start:section2_start]

    for name_token, verdict_token in _SECTION1_CANDIDATES:
        if name_token not in section1_text:
            raise VectorOssEvalGateError(
                "MISSING_CANDIDATE", f"§1 missing candidate heading '{name_token}'"
            )
        candidate_start = section1_text.find(name_token)
        next_heading = section1_text.find("### 1.", candidate_start + len(name_token))
        candidate_block = section1_text[
            candidate_start : next_heading if next_heading != -1 else len(section1_text)
        ]
        if verdict_token not in candidate_block:
            raise VectorOssEvalGateError(
                "VERDICT_DRIFT",
                f"§1 candidate '{name_token}' missing expected verdict '{verdict_token}'",
            )

    if _SECTION4_HEADING not in eval_markdown:
        raise VectorOssEvalGateError("MISSING_SECTION", f"expected heading '{_SECTION4_HEADING}'")
    if _SECTION4_CONCLUSION not in eval_markdown:
        raise VectorOssEvalGateError(
            "CONCLUSION_DRIFT", f"§4 conclusion altered or missing: {_SECTION4_CONCLUSION!r}"
        )
    if _SECTION4_NUMBA_DECISION not in eval_markdown:
        raise VectorOssEvalGateError(
            "CONCLUSION_DRIFT",
            f"§4 numba YAGNI decision altered or missing: {_SECTION4_NUMBA_DECISION!r}",
        )

    if _GATE_CLOSING_SENTENCE not in eval_markdown:
        raise VectorOssEvalGateError(
            "MISSING_GATE_SENTENCE", f"closing gate sentence missing: {_GATE_CLOSING_SENTENCE!r}"
        )

    declared = extract_declared_dependencies(pyproject_toml_text)

    banned_hit = declared & frozenset(BANNED_CODE_BORROW_PACKAGES)
    if banned_hit:
        raise VectorOssEvalGateError(
            "BANNED_CODE_BORROW_DEPENDENCY",
            f"pyproject.toml declares rejected-license package(s): {sorted(banned_hit)}",
        )

    premature_hit = declared & frozenset(PREMATURE_ADOPTION_PACKAGES)
    if premature_hit:
        raise VectorOssEvalGateError(
            "PREMATURE_ADOPTION_DEPENDENCY",
            f"pyproject.toml declares §4 YAGNI-deferred package(s): {sorted(premature_hit)}",
        )

    numpy_declared = "numpy" in declared
    if not numpy_declared:
        raise VectorOssEvalGateError(
            "NUMPY_DEPENDENCY_DRIFT",
            "§4 states BT-15 is built on numpy alone, but pyproject.toml no longer "
            "declares numpy -- update the document or restore the dependency",
        )

    return VectorOssEvalReport(
        excluded_packages=frozenset(BANNED_CODE_BORROW_PACKAGES),
        declared_dependencies=declared,
        numpy_declared=numpy_declared,
        numba_declared="numba" in declared,
    )
