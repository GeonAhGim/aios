"""Reconciliation & Resilience 순수 규칙 함수 — DB/HTTP 없이 단위 테스트
가능해야 한다.

Spec: AIOSproject 80_reconciliation_resilience_l3_build_and_operational_specification_v1.0.md §1/§2.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from src.foundation.reconciliation.domain.models import Classification, MaterialityPolicy

# 80번 §2 "Rules have ordered severity"(risk_gate/78번과 동일 원칙)를
# 집계에도 적용한다 — 항목 하나라도 더 심각하면 run/state 전체가 그 등급.
_SEVERITY_ORDER = (
    Classification.MATERIAL_MISMATCH,
    Classification.PROVIDER_UNAVAILABLE,
    Classification.MINOR_DIFFERENCE,
    Classification.HEALTHY,
)


def classify_item(
    internal_value: Decimal,
    provider_value: Decimal | None,
    policy: MaterialityPolicy,
) -> Classification:
    """80번 §1 "Missing/unreadable input yields ... never assumes zero
    balance/fill"(§2) — provider_value가 없으면 즉시 PROVIDER_UNAVAILABLE,
    0으로 취급하지 않는다."""
    if provider_value is None:
        return Classification.PROVIDER_UNAVAILABLE

    diff = abs(internal_value - provider_value)
    if diff == 0:
        return Classification.HEALTHY

    relative_base = abs(internal_value) if internal_value != 0 else abs(provider_value)
    relative_diff_pct = (diff / relative_base * 100) if relative_base != 0 else Decimal(0)

    if diff <= policy.absolute_tolerance or relative_diff_pct <= policy.relative_tolerance_pct:
        return Classification.MINOR_DIFFERENCE
    return Classification.MATERIAL_MISMATCH


def aggregate_classification(items: tuple[Classification, ...]) -> Classification:
    if not items:
        return Classification.PENDING
    present = set(items)
    for candidate in _SEVERITY_ORDER:
        if candidate in present:
            return candidate
    return Classification.HEALTHY


def compute_input_hash(
    target_ref: str, entities: dict[str, tuple[str, str]]
) -> str:
    """REC-004/006 "concurrent scheduled/manual runs dedupe ... safe retry
    does not duplicate" — 같은 target에 같은 내부/외부 값 조합이면 같은
    해시가 나온다. `entities`는 `{entity_key: (internal_value_str,
    provider_value_str)}` — Decimal을 문자열로 직렬화해 부동소수 표현
    차이로 해시가 흔들리지 않게 한다(호출부가 str(Decimal(...))로 넘김)."""
    payload = json.dumps({"target_ref": target_ref, "entities": entities}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
