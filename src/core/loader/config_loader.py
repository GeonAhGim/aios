"""5.1 — Loader.load_config().

Spec: 03_core_modules_v1.1.md#§3.1

Principle 6.5 — Do not interpret data or make investment decisions. Read only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_config(path: Path) -> dict[str, Any]:
    """Load a YAML config file (e.g. risk_policy.yaml) and return a dict.

    Schema validation (whether values are in valid ranges, etc.) is not this
    function's responsibility — each config consumer (e.g. RiskPolicyGate)
    validates against its own schema (principle 7.3).
    """
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"설정 파일의 최상위 구조가 dict가 아님: {path}")
    return data
