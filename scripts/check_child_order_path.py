"""EM-3 static proof -- child orders (an `orders` row with `parent_order_id`
set) are created only through `submit_order()` (L4-09), never by a raw
INSERT elsewhere. Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §3
("자식 주문은 예외 없이 submit_order를 통과한다"), §9 EM-3 ("자식 주문이
submit_order 경유임을 정적 증명").

Same AST/text-scan pattern as `scripts/check_compliance_gate.py` (CM-8):
run as `python scripts/check_child_order_path.py` (exit 0 = pass), and
double as pytest (this file's own `test_*` functions collect normally).

Method: scan every `INSERT INTO orders (...)` statement in `src/` (raw SQL
lives in plain strings here, not the ORM, so a regex over the column list
is simpler and no less precise than AST for this shape -- same tradeoff
`check_compliance_gate.py`'s attribute-read scan makes). Any such statement
outside `submit_order.py` whose column list mentions `parent_order_id` or
`algo_run_id` is a bypass: it would let a caller stamp child identity onto
an order without ever going through `submit_order`'s CM-8 gate check
(`decision_id`/`compliance_decision_id` required, EM-A2). A statement is
still allowed to exist elsewhere (`order_service/repository.py`,
`safety/liquidation_executor.py` predate L4-09 and never touch those two
columns) -- this check only forbids the *combination*.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SUBMIT_ORDER_FILE = "src/services/oms/application/submit_order.py"
_CHILD_COLUMNS = ("parent_order_id", "algo_run_id")

_INSERT_ORDERS_RE = re.compile(r"INSERT INTO orders\s*\(([^)]*)\)", re.IGNORECASE)


def find_insert_orders_blocks(source: str) -> list[str]:
    """Every `INSERT INTO orders (<columns>)` column-list body in `source`."""
    return [match.group(1) for match in _INSERT_ORDERS_RE.finditer(source)]


def block_sets_child_columns(columns_block: str) -> bool:
    return any(col in columns_block for col in _CHILD_COLUMNS)


def find_bypasses(rel_path: str, source: str) -> list[str]:
    """Violations for one file: `INSERT INTO orders` blocks outside
    `submit_order.py` that set `parent_order_id`/`algo_run_id`."""
    if rel_path == _SUBMIT_ORDER_FILE:
        return []
    return [
        f"{rel_path}: INSERT INTO orders(...) sets a child-identity column "
        f"(parent_order_id/algo_run_id) outside submit_order.py -- EM-A2 bypass."
        for block in find_insert_orders_blocks(source)
        if block_sets_child_columns(block)
    ]


def _iter_production_files() -> list[Path]:
    return sorted(
        p
        for p in (_REPO_ROOT / "src").rglob("*.py")
        if p.is_file() and "__pycache__" not in p.parts
    )


def test_no_child_order_insert_bypasses_submit_order() -> None:
    """EM-A2 -- no `src/` file other than `submit_order.py` INSERTs into
    `orders` with `parent_order_id`/`algo_run_id` set."""
    violations: list[str] = []
    for path in _iter_production_files():
        rel = path.relative_to(_REPO_ROOT).as_posix()
        violations.extend(find_bypasses(rel, path.read_text(encoding="utf-8")))
    assert violations == [], "\n".join(violations)


def test_submit_order_still_sets_child_columns() -> None:
    """Regression guard for the check itself -- if `submit_order.py`'s
    INSERT ever drops `parent_order_id`/`algo_run_id`, EM-3's canonical
    identity columns (073beca589d5) would silently stop being wired, and
    the bypass check above would have nothing left to allowlist."""
    source = (_REPO_ROOT / _SUBMIT_ORDER_FILE).read_text(encoding="utf-8")
    blocks = find_insert_orders_blocks(source)
    assert blocks and any(block_sets_child_columns(block) for block in blocks), (
        f"{_SUBMIT_ORDER_FILE}: no INSERT INTO orders(...) sets parent_order_id/"
        "algo_run_id anymore -- child order identity is no longer wired."
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서 한글 깨짐 방지
    try:
        test_submit_order_still_sets_child_columns()
        test_no_child_order_insert_bypasses_submit_order()
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        return 1
    print("OK: EM-3 child-order INSERT 경로 정적 검사 통과 (submit_order 경유 증명)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
