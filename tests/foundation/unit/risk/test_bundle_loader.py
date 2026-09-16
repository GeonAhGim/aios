"""U-15 personal-conservative YAML 로더 단위테스트."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.foundation.risk.adapters.bundle_loader import (
    DEFAULT_PERSONAL_BUNDLE_PATH,
    load_personal_bundle,
)

_VALID_CONFIG = {
    "name": "personal-conservative",
    "version": 1,
    "position_pct_of_equity": 0.02,
    "daily_loss_kill_pct": 0.03,
    "max_exposure_pct": 0.30,
    "default_notional_cap_krw": 1000000,
    "symbol_whitelist": ["BTC/USDT"],
    "exchange_notional_caps": {"bitget": 500000},
}


def _write_yaml(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "bundle.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_default_bundle_file_exists_and_loads():
    assert DEFAULT_PERSONAL_BUNDLE_PATH.exists()

    bundle = load_personal_bundle()

    assert bundle.name == "personal-conservative"
    assert bundle.position_pct_of_equity == Decimal("0.02")


def test_loads_valid_config_into_domain_bundle(tmp_path: Path):
    path = _write_yaml(tmp_path, _VALID_CONFIG)

    bundle = load_personal_bundle(path)

    assert bundle.position_pct_of_equity == Decimal("0.02")
    assert bundle.daily_loss_kill_pct == Decimal("0.03")
    assert bundle.max_exposure_pct == Decimal("0.3")
    assert bundle.default_notional_cap_krw == Decimal("1000000")
    assert bundle.symbol_whitelist == frozenset({"BTC/USDT"})
    assert bundle.exchange_notional_caps == {"bitget": Decimal("500000")}


def test_unknown_key_is_rejected_fail_closed(tmp_path: Path):
    config = dict(_VALID_CONFIG)
    config["totally_unknown_field"] = 1
    path = _write_yaml(tmp_path, config)

    with pytest.raises(ValidationError):
        load_personal_bundle(path)


def test_out_of_range_pct_is_rejected(tmp_path: Path):
    config = dict(_VALID_CONFIG)
    config["position_pct_of_equity"] = 1.5
    path = _write_yaml(tmp_path, config)

    with pytest.raises(ValidationError):
        load_personal_bundle(path)
