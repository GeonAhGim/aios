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
    "transitions": [{"from_state": "IDLE", "to_state": "HOLDING", "condition": "rsi < 30"}],
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


def test_load_strategy_file_missing_required_field_raises(tmp_path: Path):
    incomplete = dict(VALID_STRATEGY)
    del incomplete["author_agent"]
    strategy_file = tmp_path / "incomplete.json"
    strategy_file.write_text(json.dumps(incomplete), encoding="utf-8")

    with pytest.raises(ValueError):
        load_strategy_file(strategy_file)


def test_load_strategy_file_invalid_enum_state_raises(tmp_path: Path):
    invalid = dict(VALID_STRATEGY)
    invalid["states"] = ["IDLE", "NOT_A_REAL_STATE"]
    strategy_file = tmp_path / "invalid_enum.json"
    strategy_file.write_text(json.dumps(invalid), encoding="utf-8")

    with pytest.raises(ValueError):
        load_strategy_file(strategy_file)


def test_load_strategy_file_nonexistent_path_raises(tmp_path: Path):
    missing_file = tmp_path / "does_not_exist.json"

    with pytest.raises(FileNotFoundError):
        load_strategy_file(missing_file)


def test_load_strategy_file_read_failure_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    strategy_file = tmp_path / "strategy.json"
    strategy_file.write_text(json.dumps(VALID_STRATEGY), encoding="utf-8")

    def _raise_os_error(self: Path, encoding: str | None = None) -> str:
        raise OSError("simulated read failure")

    monkeypatch.setattr(Path, "read_text", _raise_os_error)

    with pytest.raises(OSError):
        load_strategy_file(strategy_file)
