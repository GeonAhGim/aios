"""No `class X(str, Enum)` may reappear in src/ (ADR-2026-10-29-A).

On Python 3.11+ `format()`/f-string of a `(str, Enum)` yields `ClassName.MEMBER` while 3.10
yielded the value, so f-string output silently changed across the 3.13 migration.
`enum.StrEnum` gives the value for str()/format()/f-string on every version. ruff's UP042
reports the same thing, but only once `target-version` is py313 (deferred until the remaining
UP rules are cleaned), so this test keeps the rule enforced in the meantime.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "src"


def _is_mixed_str_enum(node: ast.ClassDef) -> bool:
    names = {ast.unparse(base) for base in node.bases}
    return "str" in names and bool(names & {"Enum", "enum.Enum"})


def test_src_has_no_str_enum_mixin_classes() -> None:
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders.extend(
            f"{path.relative_to(SRC.parent)}:{node.lineno} {node.name}"
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and _is_mixed_str_enum(node)
        )
    assert offenders == [], "use enum.StrEnum instead of (str, Enum): " + ", ".join(offenders)
