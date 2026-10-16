"""5.2 — Loader.load_strategy_file().

Spec: 03_core_modules_v1.1.md#§3.1

Strategy file format is JSON — identical to strategies.fsm_definition (JSONB, column 04)
in the DB, so bidirectional conversion is natural (spec 03 did not name the format).
"""

from __future__ import annotations

from pathlib import Path

from src.data.models.strategy_fsm import FSMStrategyConfig


def load_strategy_file(path: Path) -> FSMStrategyConfig:
    """Read a strategy definition JSON file, validate it as FSMStrategyConfig, and return it.

    Format errors (JSON parse failure, schema mismatch) propagate as Pydantic ValidationError
    — do not swallow exceptions or fall back to defaults here.
    """
    return FSMStrategyConfig.model_validate_json(path.read_text(encoding="utf-8"))
