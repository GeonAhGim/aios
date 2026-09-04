"""FD-8.3 — 리스크 검사(RiskEngine, 가장 중요). L4_risk_and_safety_v1.0.md§9 R-17.

하위호환 facade — 판정·순서·단락은 `evaluate()`(R-16)에만 있고 재구현하지 않는다.
`check()`는 legacy dict->`RiskInputs` 변환 위임 어댑터, `check_decision()`이 신규
공개 계약이다. (I9: LLM 미import.)
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.portfolio.models import AllocationDecision
from src.core.risk.decision import GateKind, RiskDecision, RiskOutcome, RuleResult
from src.core.risk.evaluator import evaluate
from src.core.risk.inputs import RiskInputs
from src.core.risk.models import RiskCheckResult
from src.core.risk.policy_bundle import BundleState, RiskRuleBundle, compute_rule_hash

_ENGINE_VERSION = "legacy-facade-v1"
_BUNDLE_ID = UUID("8f9a6f0e-6b8e-4b1a-9a1d-1a2b3c4d5e6f")  # 고정 상수(난수 아님)
_TENANT_ID = UUID("2b6a9f1a-0c1b-4a2d-8e3f-4a5b6c7d8e9f")
_APPROVER_ID = UUID("5d4c3b2a-1e0f-4a9b-8c7d-6e5f4a3b2c1d")

# legacy checked_rules 이름 -> R-16 규칙 id, legacy 평가 순서 그대로.
_LEGACY_ORDER = (
    ("daily_loss", "daily_loss"), ("max_drawdown", "max_drawdown"), ("leverage", "leverage"),
    ("position_concentration", "concentration"), ("strategy_allocation", "strategy_allocation"),
    ("var", "var_es"), ("correlation_risk", "correlation"), ("trade_frequency", "trade_frequency"),
    ("safety_state", "safety_state"),
)

# legacy엔 warning 구간이 없어 규칙당 문구 하나로 합류(나머지는 "_exceeded" 기본값).
_EXCEEDED_SUFFIX = {
    "daily_loss": "halt_exceeded", "max_drawdown": "hard_stop_exceeded",
    "trade_frequency": "anomaly",
}


class RiskEngine:
    def __init__(self, policy: RiskPolicy) -> None:
        self._policy = policy

    def check(
        self, allocation: AllocationDecision, account_state: dict[str, Any]
    ) -> RiskCheckResult:
        inputs = self._bridge_legacy_inputs(allocation, account_state)
        return _to_legacy_result(self.check_decision(inputs))

    def check_decision(self, inputs: RiskInputs) -> RiskDecision:
        """R-17 신규 공개 계약 — R-16 `evaluate()`를 그대로 호출·위임한다."""
        policy = self._policy
        bundle = RiskRuleBundle(
            id=_BUNDLE_ID, version=policy.version, state=BundleState.ACTIVE,
            rule_hash=compute_rule_hash(policy, _ENGINE_VERSION), engine_version=_ENGINE_VERSION,
            policy_snapshot=policy.model_dump(mode="python"),
            created_by=_APPROVER_ID, approved_by=_APPROVER_ID,
        )
        return evaluate(
            inputs, bundle, gate_kind=GateKind.PRE_TRADE, trace_id=uuid4(),
            now=inputs.as_of, ttl=policy.decision_ttl.pre_trade_sec,
        )

    def _bridge_legacy_inputs(
        self, allocation: AllocationDecision, account_state: dict[str, Any]
    ) -> RiskInputs:
        """legacy dict -> `RiskInputs`(값 변환이지 규칙 재구현이 아니다): 진입에만
        집중도 적용(reduce_only 파생), legacy capital_pct를 그대로 재현하는
        시가평가 파생, legacy에 없던 안전/VaR/상관 신규 입력은 중립값으로 채운다."""
        now = datetime.now(timezone.utc)
        inputs = RiskInputs.from_legacy_dict(
            allocation, account_state, tenant_id=_TENANT_ID, execution_id=0, now=now
        )
        qty = account_state.get("position_quantity")
        reduce_only = qty is not None and qty != 0
        total_equity = inputs.equity.total_equity
        smv = total_equity * inputs.intent.capital_pct / Decimal("100") if total_equity else None
        return inputs.model_copy(update={
            "intent": inputs.intent.model_copy(update={"reduce_only": reduce_only}),
            "exposure": inputs.exposure.model_copy(update={"symbol_market_value": smv}),
            "stats": inputs.stats.model_copy(update={
                "es_pct": inputs.stats.var_pct, "var_method": "legacy_facade",
                "bars_used": self._policy.var.min_bars, "max_correlation": 1.0,
            }),
            "safety": inputs.safety.model_copy(update={
                "active_control_scopes": (), "data_distrust_level": "TRUSTED",
                "connection_fresh": True,
            }),
        })


def _to_legacy_result(decision: RiskDecision) -> RiskCheckResult:
    results_by_rule = {r.rule_id: r for r in decision.rule_results}
    checked_rules: list[str] = []
    rejection_reason: str | None = None
    for legacy_name, rule_id in _LEGACY_ORDER:
        result = results_by_rule.get(rule_id)
        if result is None:
            continue
        checked_rules.append(legacy_name)
        if result.outcome != RiskOutcome.ALLOW:
            rejection_reason = _reason_for(legacy_name, result)
            break
    approved = decision.outcome == RiskOutcome.ALLOW
    return RiskCheckResult(
        approved=approved, rejection_reason=None if approved else rejection_reason,
        checked_rules=checked_rules, decision_id=decision.decision_id,
    )


def _reason_for(legacy_name: str, result: RuleResult) -> str:
    reason_code = result.reason_code or ""
    if reason_code.startswith("RISK_INPUT_MISSING") or reason_code.startswith("RISK_RULE_ERROR"):
        return f"{legacy_name}_data_unavailable"
    if legacy_name == "safety_state":
        return "safety_state_blocked"
    return f"{legacy_name}_{_EXCEEDED_SUFFIX.get(legacy_name, 'exceeded')}"
