"""5.1 — Loader.load_config().

Spec: 03_core_modules_v1.1.md#§3.1

6.5 원칙 — 데이터를 해석하거나 투자판단을 하지 않는다. 읽기만 한다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_config(path: Path) -> dict[str, Any]:
    """YAML 설정 파일(risk_policy.yaml 등)을 읽어 dict로 반환한다.

    스키마 검증(값이 올바른 범위인지 등)은 이 함수의 책임이 아니다 — 각
    설정 소비자(예: RiskPolicyGate)가 자신의 스키마로 검증한다(7.3 원칙).
    """
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"설정 파일의 최상위 구조가 dict가 아님: {path}")
    return data
