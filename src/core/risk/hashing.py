"""L4_risk_and_safety_v1.0.md#9 R-01 — canonical JSON + sha256.

RiskDecision/RiskInputs의 결정론적 재생(RSK-001/009)이 성립하려면 같은
논리적 값은 항상 같은 바이트열로 직렬화돼야 한다. 표준 `json.dumps`는
dict 키 순서와 `Decimal`의 원래 문자열 표현(trailing zero 등)에 따라
출력이 달라지므로 그대로는 쓸 수 없다 — 여기서 키 정렬과 `Decimal`
정규화를 강제한다.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID


def _normalize(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        # normalize()로 trailing zero를 제거한 뒤 'f' 포맷으로 지수 표기(1E+2)를
        # 막는다 — Decimal("1.0")과 Decimal("1.00")이 같은 문자열이 되게 한다.
        return format(obj.normalize(), "f")
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            raise ValueError("canonical_json: naive datetime은 허용하지 않는다")
        return obj.astimezone(timezone.utc).isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _normalize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_normalize(v) for v in obj]
    return obj


def canonical_json(obj: Any) -> bytes:
    """키 정렬 + `Decimal`/`datetime`/`UUID` 정규화가 적용된 결정론적 JSON 바이트열."""
    normalized = _normalize(obj)
    return json.dumps(
        normalized, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
