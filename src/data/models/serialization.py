"""2.2 — Decimal 안전 JSON 인코더.

Spec: 01_data_models_v1.3.md#§1.6
"""
from __future__ import annotations

import json
from decimal import Decimal


class DecimalSafeEncoder(json.JSONEncoder):
    """Decimal을 문자열로 직렬화 — float 변환 금지(정밀도 손실 방지)."""

    def default(self, obj: object) -> object:
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)
