"""IND-9 deepen (task-2919) — INDICATOR_OSS_EVAL.md DoD gate (pure).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 IND-9.
ADR-2026-09-05-A: GPL/LGPL code must never be borrowed into AIOS, and IND-10/11
may not be assigned before this leaf's scorecard is approved. The scorecard
itself (docs/design/INDICATOR_OSS_EVAL.md) is a pure document with no code to
exercise, so the machine-verifiable regression this gate adds is a cross-check
between the document's §5/§6 conclusions and the *actual* declared dependencies
in `pyproject.toml`: if a future change adds any of the GPL/LGPL packages the
document excludes (tulipy, tulipindicators, backtrader, nautilus_trader) as a
project dependency, this gate turns red. No I/O — callers pass in file text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "BANNED_LICENSE_PACKAGES",
    "OssEvalGateError",
    "OssEvalReport",
    "extract_declared_dependencies",
    "assert_oss_eval_gate",
]

# §5 GPL/LGPL exclusion table — normalized pip distribution name -> expected
# license family. Frozen: changing membership here is a licensing decision,
# not a routine edit, and must be paired with a matching doc update.
BANNED_LICENSE_PACKAGES: dict[str, str] = {
    "tulipy": "LGPL-3.0",
    "tulipindicators": "LGPL-3.0",
    "backtrader": "GPL-3.0",
    "nautilus-trader": "LGPL-3.0-or-later",
}

# (name token in §5 prose, license token that must co-occur in the same table)
_SECTION5_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("tulipy", "LGPL-3.0"),
    ("backtrader", "GPL-3.0"),
    ("nautilus_trader", "LGPL-3.0"),
)

_SECTION6_CONCLUSIONS: tuple[str, ...] = (
    "### IND-10 (TA-Lib 브리지): **반입 가**",
    "### IND-11 (pandas-ta 브리지): **명세 원안(pandas-ta 원본) 불가, 대체안 조건부 가**",
    "### ta: **브리지 불필요**",
)

_GATE_CLOSING_SENTENCE = "GPL/LGPL 3종은 어떤 형태로도 코드가 저장소에 들어오지 않는다."


class OssEvalGateError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class OssEvalReport:
    excluded_packages: frozenset[str]
    declared_dependencies: frozenset[str]
    talib_declared: bool


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
        raise OssEvalGateError("MISSING_PROJECT_TABLE", "pyproject.toml has no [project] table")
    block = match.group(1)
    names: set[str] = set()
    for requirement in re.findall(r'"([^"]+)"', block):
        name_match = re.match(r"^\s*([A-Za-z][A-Za-z0-9._-]*)", requirement)
        if name_match:
            names.add(_normalize_name(name_match.group(1)))
    if not names:
        raise OssEvalGateError(
            "EMPTY_DEPENDENCY_LIST", "[project] table yielded no dependency names"
        )
    return frozenset(names)


def assert_oss_eval_gate(eval_markdown: str, pyproject_toml_text: str) -> OssEvalReport:
    """Full IND-9 DoD gate. Returns the report on success; raises on violation."""
    if not eval_markdown.strip():
        raise OssEvalGateError("EMPTY_DOCUMENT", "INDICATOR_OSS_EVAL.md is empty")

    section5_start = eval_markdown.find("## 5. GPL/LGPL")
    section6_start = eval_markdown.find("## 6. 채점표")
    if section5_start == -1 or section6_start == -1 or section6_start < section5_start:
        raise OssEvalGateError(
            "MISSING_SECTION",
            "expected '## 5. GPL/LGPL ...' followed by '## 6. 채점표 ...'",
        )
    section5_text = eval_markdown[section5_start:section6_start]

    for name_token, license_token in _SECTION5_CANDIDATES:
        if name_token not in section5_text:
            raise OssEvalGateError(
                "MISSING_EXCLUDED_CANDIDATE",
                f"§5 table missing excluded candidate '{name_token}'",
            )
        if license_token not in section5_text:
            raise OssEvalGateError(
                "MISSING_LICENSE_CITATION",
                f"§5 table missing '{license_token}' next to '{name_token}'",
            )

    for heading in _SECTION6_CONCLUSIONS:
        if heading not in eval_markdown:
            raise OssEvalGateError(
                "CONCLUSION_DRIFT",
                f"scorecard conclusion heading missing or altered: {heading!r}",
            )

    if _GATE_CLOSING_SENTENCE not in eval_markdown:
        raise OssEvalGateError(
            "MISSING_GATE_SENTENCE",
            f"closing gate sentence missing: {_GATE_CLOSING_SENTENCE!r}",
        )

    declared = extract_declared_dependencies(pyproject_toml_text)
    banned_hit = declared & frozenset(BANNED_LICENSE_PACKAGES)
    if banned_hit:
        raise OssEvalGateError(
            "BANNED_LICENSE_DEPENDENCY",
            f"pyproject.toml declares excluded GPL/LGPL package(s): {sorted(banned_hit)}",
        )

    talib_declared = "ta-lib" in declared
    if not talib_declared:
        raise OssEvalGateError(
            "TALIB_DEPENDENCY_DRIFT",
            "§0 states TA-Lib is already admitted, but pyproject.toml no longer "
            "declares it -- update the document or restore the dependency",
        )

    return OssEvalReport(
        excluded_packages=frozenset(BANNED_LICENSE_PACKAGES),
        declared_dependencies=declared,
        talib_declared=talib_declared,
    )
