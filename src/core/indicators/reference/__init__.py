"""IND-7g — reference vector three-way cross-verification package.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.3 IND-7g.
The actual logic lives in `verify_all.py`. This file is only a package
marker and re-exports nothing — to avoid circular imports, import the
submodule explicitly, e.g. `from src.core.indicators.reference import
verify_all`.
"""
from __future__ import annotations
