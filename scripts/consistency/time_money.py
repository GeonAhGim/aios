"""CONSIST-1 시간/금액 검사군 -- naive_datetime / money_float /
symbol_id_assembly / authority_duplication. task-3725 CONSIST-1c로
check_consistency.py에서 분리(순수 이동, 판정 로직 변경 없음).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from scripts.consistency.common import Hit, _iter_py_files, _safe_parse

# ---------------------------------------------------------------------------
# 9. naive_datetime
# ---------------------------------------------------------------------------


def _dotted_chain(func: ast.expr) -> str:
    parts: list[str] = []
    cur = func
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def check_naive_datetime(root: Path) -> list[Hit]:
    hits: list[Hit] = []
    for path in _iter_py_files(root, "src"):
        tree = _safe_parse(path)
        if tree is None:
            continue
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            attr = func.attr if isinstance(func, ast.Attribute) else None
            if attr not in ("now", "utcnow"):
                continue
            if "datetime" not in _dotted_chain(func).split("."):
                continue
            if attr == "utcnow":
                hits.append((rel, node.lineno))
                continue
            has_tz = bool(node.args) or any(kw.arg == "tz" for kw in node.keywords)
            if not has_tz:
                hits.append((rel, node.lineno))
    return hits


# ---------------------------------------------------------------------------
# 10. money_float
# ---------------------------------------------------------------------------

_MONEY_KEYWORDS = (
    "amount",
    "price",
    "balance",
    "fee",
    "cost",
    "notional",
    "cash",
    "pnl",
    "equity",
    "margin",
    "quantity",
    "qty",
    "proceeds",
    "payout",
    "principal",
)


def _is_money_name(name: str) -> bool:
    low = name.lower()
    return any(kw in low for kw in _MONEY_KEYWORDS)


def _is_float_annotation(node: ast.expr | None) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id == "float"
    if isinstance(node, ast.Subscript):
        return _is_float_annotation(node.slice)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _is_float_annotation(node.left) or _is_float_annotation(node.right)
    return False


# A public contract (e.g. `src/foundation/*/contracts/v1.py`) is a wire schema
# whose field types are a compatibility surface (ADR-2026-09-10-C P5 --
# in-place type changes to a published v1 contract are a hard veto, not a
# ratchet). Such a field stays float on the wire and is converted to Decimal
# at the adapter/application boundary instead of at the contract itself --
# `check_money_float` accepts that as compliant when the line carries an
# explicit `# ratchet-allow: wire-boundary: <reason>` marker (task-5762, CTO
# decision 2026-09-23), same "explicit escape hatch, not a blanket exemption"
# shape as `_ratchet_allow_reason` in common.py.
_WIRE_BOUNDARY_ALLOW_RE = re.compile(r"#\s*ratchet-allow:\s*wire-boundary:\s*(\S.*)")


def _is_wire_boundary_allowed(source_lines: list[str], lineno: int) -> bool:
    """Marker may sit on the field's own line, or on the line directly above
    it (a standalone comment) when the inline form would overflow the repo's
    line-length limit."""
    for candidate in (lineno, lineno - 1):
        if 1 <= candidate <= len(source_lines) and _WIRE_BOUNDARY_ALLOW_RE.search(
            source_lines[candidate - 1]
        ):
            return True
    return False


def check_money_float(root: Path) -> list[Hit]:
    hits: list[Hit] = []
    for path in _iter_py_files(root, "src"):
        tree = _safe_parse(path)
        if tree is None:
            continue
        rel = path.relative_to(root).as_posix()
        source_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if (
                    _is_float_annotation(node.annotation)
                    and _is_money_name(node.target.id)
                    and not _is_wire_boundary_allowed(source_lines, node.lineno)
                ):
                    hits.append((rel, node.lineno))
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for arg in [*node.args.args, *node.args.kwonlyargs]:
                    if (
                        _is_float_annotation(arg.annotation)
                        and _is_money_name(arg.arg)
                        and not _is_wire_boundary_allowed(source_lines, arg.lineno)
                    ):
                        hits.append((rel, node.lineno))
    return hits


# ---------------------------------------------------------------------------
# 11. symbol_id_assembly (FA-0d position_key 규칙 확장)
# ---------------------------------------------------------------------------

_ASSEMBLY_TARGET_NAMES = frozenset({"symbol", "instrument_id"})
_EXEMPT_DIR_PARTS = (("src", "db", "migrations"),)


def _is_assembly_expr(node: ast.expr) -> bool:
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Mod):
        return True
    return bool(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
    )


def _assign_target_value(node: ast.AST) -> tuple[list[ast.expr], ast.expr | None, int]:
    if isinstance(node, ast.Assign):
        return list(node.targets), node.value, node.lineno
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return [node.target], node.value, node.lineno
    return [], None, 0


def _assign_assembly_matches(node: ast.AST, target_names: frozenset[str]) -> list[tuple[str, int]]:
    targets, value, lineno = _assign_target_value(node)
    if value is None or not _is_assembly_expr(value):
        return []
    return [(t.id, lineno) for t in targets if isinstance(t, ast.Name) and t.id in target_names]


def _call_kw_assembly_matches(
    node: ast.Call, target_names: frozenset[str]
) -> list[tuple[str, int]]:
    return [
        (kw.arg, kw.value.lineno)
        for kw in node.keywords
        if kw.arg in target_names and _is_assembly_expr(kw.value)
    ]


def _assembly_matches(node: ast.AST, target_names: frozenset[str]) -> list[tuple[str, int]]:
    matches = _assign_assembly_matches(node, target_names)
    if isinstance(node, ast.Call):
        matches = [*matches, *_call_kw_assembly_matches(node, target_names)]
    return matches


def check_symbol_id_assembly(root: Path) -> list[Hit]:
    hits: list[Hit] = []
    for path in _iter_py_files(root, "src"):
        rel_parts = path.relative_to(root).parts
        if any(rel_parts[: len(prefix)] == prefix for prefix in _EXEMPT_DIR_PARTS):
            continue
        tree = _safe_parse(path)
        if tree is None:
            continue
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            for _name, lineno in _assembly_matches(node, _ASSEMBLY_TARGET_NAMES):
                hits.append((rel, lineno))
    return hits


# ---------------------------------------------------------------------------
# 13. authority_duplication (RATCHET-2, task-3256)
# ---------------------------------------------------------------------------

_AUTHORITY_TARGET_NAMES = frozenset({"idempotency_key"})


def _bounded_context(rel_parts: tuple[str, ...]) -> str | None:
    """`src/foundation/<agg>/...` -> `foundation.<agg>`, `src/core/<d>/...` -> `core.<d>`."""
    if len(rel_parts) < 3:
        return None
    if rel_parts[0] == "src" and rel_parts[1] in ("foundation", "core"):
        return f"{rel_parts[1]}.{rel_parts[2]}"
    return None


def check_authority_duplication(root: Path) -> list[Hit]:
    """같은 bounded context 안에서 같은 이름의 "authority"(멱등키 등)를 서로 다른
    파일이 각자 raw assembly(f-string/concat/join)로 재조립하면 위반 --
    `record_fill.py`가 `journal_rules.py`의 `fill:` 멱등키 스킴을 독립적으로
    재구성한 사례가 실제 동기다(FA 축, task-3256 조사). 캔노니컬 빌더 호출이나
    기존 값을 그대로 전달하는 것은 위반이 아니다 -- raw assembly만 잡는다
    (`check_position_key_central.py`와 같은 판정 원칙: 완벽한 판정을 목표하지
    않고, 늘어나는 것만 baseline으로 막는다)."""
    by_context_target: dict[tuple[str, str], list[Hit]] = {}
    for path in _iter_py_files(root, "src"):
        rel_parts = path.relative_to(root).parts
        if any(rel_parts[: len(prefix)] == prefix for prefix in _EXEMPT_DIR_PARTS):
            continue
        context = _bounded_context(rel_parts)
        if context is None:
            continue
        tree = _safe_parse(path)
        if tree is None:
            continue
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            for name, lineno in _assembly_matches(node, _AUTHORITY_TARGET_NAMES):
                key = (context, name)
                by_context_target.setdefault(key, []).append((rel, lineno))

    hits: list[Hit] = []
    for sites in by_context_target.values():
        distinct_files = {rel for rel, _ in sites}
        if len(distinct_files) >= 2:
            hits.extend(sites)
    return hits
