"""Risk & Safety Gate 순수 규칙 함수 — DB/HTTP 없이 단위 테스트 가능해야 한다.

Spec: AIOSproject 78_risk_safety_l3_build_and_operational_specification_v1.0.md §2/§3,
48_risk_safety_gate_and_kill_switch_specification_v1.0.md §5.
"""
from __future__ import annotations

import hashlib

from src.foundation.risk_gate.domain.models import (
    RiskEvaluationInput,
    RiskOutcome,
    SafetyControl,
    SafetyControlState,
    SafetyScope,
)

RULE_VERSION = "v1"

# 78번 §2 "Rules have ordered severity: global/provider stop > tenant/
# account/deployment stop > ...". 이 튜플의 순서가 곧 우선순위다 — 여러
# scope가 동시에 ACTIVE여도 가장 앞선 것의 reason_code가 대표로 먼저 온다
# (전부 DENY로 수렴하므로 outcome 자체는 바뀌지 않지만, 운영자가 로그에서
# "무엇 때문에 막혔는지" 볼 때 결정론적 순서가 필요하다).
_SCOPE_SEVERITY_ORDER = (
    SafetyScope.GLOBAL,
    SafetyScope.PROVIDER,
    SafetyScope.TENANT,
    SafetyScope.ACCOUNT,
    SafetyScope.STRATEGY_DEPLOYMENT,
)


def compose_safety_controls(
    controls: tuple[SafetyControl, ...],
) -> tuple[RiskOutcome | None, list[str]]:
    """78번 §2/48번 §4 — 활성 safety control이 하나라도 있으면 DENY다(kill
    switch에는 "일부만 통과" 같은 절충이 없다). 없으면 (None, [])를 반환해
    호출부가 다음 단계(mandate/connection) 판단으로 넘어가게 한다."""
    active = [c for c in controls if c.state == SafetyControlState.ACTIVE]
    if not active:
        return None, []

    active_by_scope = {c.scope: c for c in active}
    reasons = [
        f"RISK_KILL_SWITCH_ACTIVE_{scope.value}"
        for scope in _SCOPE_SEVERITY_ORDER
        if scope in active_by_scope
    ]
    return RiskOutcome.DENY, reasons


def evaluate_risk(input: RiskEvaluationInput) -> tuple[RiskOutcome, list[str], list[str]]:
    """78번 §2 결정 트리 — (outcome, reason_codes, obligations).

    우선순위(48번 §5 acceptance test 2 — "각각 독립적으로 stop/pause를
    유발"): safety control(kill switch) > mandate 미존재/거부 > connection
    staleness > 정상(ALLOW).
    """
    control_outcome, control_reasons = compose_safety_controls(input.active_controls)
    if control_outcome is not None:
        return control_outcome, control_reasons, []

    if not input.mandate_available:
        # 78번 §1 "Missing/unreadable input yields DENY or PAUSE, never
        # implicit allow."
        return RiskOutcome.DENY, ["RISK_INPUT_MANDATE_MISSING"], []

    if input.mandate_blocking:
        reasons = list(input.mandate_reason_codes) or ["POLICY_MANDATE_BLOCKED"]
        return RiskOutcome.DENY, reasons, []

    if input.connection_fresh is False:
        return RiskOutcome.PAUSE, ["RISK_INPUT_STALE"], ["REQUIRE_FRESH_CONNECTION"]

    return RiskOutcome.ALLOW, [], []


def compute_subject_fingerprint(tenant_id: str, gate_kind: str, payload: str) -> str:
    """RSK-001 "pinned input/rule produces stable decision/fingerprint" —
    같은 입력이면 항상 같은 fingerprint, 즉 캐시 재사용이 가능해야 한다."""
    combined = f"{tenant_id}|{gate_kind}|{RULE_VERSION}|{payload}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
