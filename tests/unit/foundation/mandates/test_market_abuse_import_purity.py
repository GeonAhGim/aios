"""CM-10 import-purity gates: market_abuse must not couple to CM-9's
wash_trade rule module, and vice-versa.  Split from test_market_abuse.py
to keep the parent under the 500-line ratchet.
"""

from __future__ import annotations

import ast
from pathlib import Path

from src.foundation.mandates.domain import market_abuse


def _imported_module_names(source: str) -> set[str]:
    """Extract top-level import names from *source* via AST."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
            names.update(alias.name for alias in node.names)
    return names


def test_market_abuse_does_not_import_or_share_logic_with_wash_trade_rule():
    """market_abuse.py must not import or share logic with CM-9's
    pre-trade wash_trade rule.  This is a static-analysis gate:
    an AST scan of the source must find no ``wash_trade`` import.
    """
    source = Path(market_abuse.__file__).read_text(encoding="utf-8")
    imported = _imported_module_names(source)
    assert not any("wash_trade" in name for name in imported)

    pre_trade_wash_trade = Path(market_abuse.__file__).parent / "rules" / "wash_trade.py"
    if pre_trade_wash_trade.exists():
        # CM-9 (task-2458) had not landed on main as of this leaf; once it
        # has, also assert the pre-trade rule does not reach back into this
        # post-trade module.
        other_imported = _imported_module_names(pre_trade_wash_trade.read_text(encoding="utf-8"))
        assert not any("market_abuse" in name for name in other_imported)
