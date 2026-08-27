from pathlib import Path

import pytest

from src.core.loader.config_loader import load_config


def test_load_config_reads_yaml(tmp_path: Path):
    config_file = tmp_path / "risk_policy.yaml"
    config_file.write_text(
        "version: draft-1\ndaily_loss:\n  warning_pct: 3.0\n  halt_pct: 5.0\n",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config["version"] == "draft-1"
    assert config["daily_loss"]["warning_pct"] == 3.0


def test_load_config_empty_file_returns_empty_dict(tmp_path: Path):
    config_file = tmp_path / "empty.yaml"
    config_file.write_text("", encoding="utf-8")

    assert load_config(config_file) == {}


def test_load_config_non_mapping_root_raises(tmp_path: Path):
    config_file = tmp_path / "list.yaml"
    config_file.write_text("- a\n- b\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_config(config_file)
