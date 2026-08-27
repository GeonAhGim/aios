import json
from pathlib import Path

import pytest

from src.core.loader.strategy_loader import load_strategy_file

VALID_STRATEGY = {
    "strategy_id": "strat-1",
    "version": "v1.0",
    "target_asset": "BTC/USDT",
    "market": "crypto",
    "exchange": "bitget",
    "states": ["IDLE", "HOLDING"],
    "transitions": [
        {"from_state": "IDLE", "to_state": "HOLDING", "condition": "rsi < 30"}
    ],
    "author_agent": "strategy-research-agent",
}


def test_load_strategy_file_roundtrip(tmp_path: Path):
    strategy_file = tmp_path / "strategy.json"
    strategy_file.write_text(json.dumps(VALID_STRATEGY), encoding="utf-8")

    config = load_strategy_file(strategy_file)

    assert config.strategy_id == "strat-1"
    assert config.transitions[0].condition == "rsi < 30"


def test_load_strategy_file_invalid_json_raises(tmp_path: Path):
    strategy_file = tmp_path / "broken.json"
    strategy_file.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(ValueError):
        load_strategy_file(strategy_file)
