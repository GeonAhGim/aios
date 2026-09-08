"""L4_risk_and_safety_v1.0.md#§2 112행, §9 R-54 — 저장된 결정 재계산·대조.

Spec: 선행 R-16(`src/core/risk/evaluator.py`), R-22(`postgres_bundle_repository.py`),
R-24(`postgres_decision_repository.py`). WORM 행에 저장된 `inputs_snapshot`과,
그 결정이 실제로 사용한 `rule_hash`에 매칭되는 rule_bundle을 "pinned" 입력으로
그대로 R-16 `evaluate()`에 통과시켜 재판정을 얻는다 — 판정 로직(임계값 비교)은
여기서 재구현하지 않는다(§C 중복 컨텍스트 금지). 불일치는 §5·§7
`aios.core_risk.replay_mismatch.count_total` 1 증가로 관측되고,
`INTEGRITY_RISK_REPLAY_MISMATCH`(§ 에러 taxonomy 295행)로 호출자
(`src/tools/risk_replay.py`)가 exit code로 표면화한다.

`decision_repo`/`bundle_repo`는 구조적 타입(Protocol)만 요구한다 — 기존
`PostgresDecisionRepository.get()`/`PostgresBundleRepository.get_by_rule_hash()`가
그대로 만족한다(별도 어댑터 불필요).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from src.core.observability.metric_names import CORE_RISK_REPLAY_MISMATCH_COUNT_TOTAL
from src.core.observability.metrics_registry import get_registry
from src.core.risk.decision import RiskDecision
from src.core.risk.evaluator import evaluate
from src.core.risk.inputs import RiskInputs
from src.core.risk.policy_bundle import RiskRuleBundle

INTEGRITY_RISK_REPLAY_MISMATCH = "INTEGRITY_RISK_REPLAY_MISMATCH"

# 재계산 결과와 저장된 결정을 대조할 필드 — 전부 evaluator의 "판정 산출물"이지
# 재계산 입력(rule_version/rule_hash/engine_version은 같은 bundle에서 나오므로
# 항상 일치, inputs_hash는 같은 inputs_snapshot에서 나오므로 항상 일치)이 아니다.
_COMPARED_FIELDS: tuple[str, ...] = (
    "decision_id",
    "outcome",
    "reason_codes",
    "obligations",
    "rule_results",
)


class DecisionSource(Protocol):
    async def get(self, decision_id: UUID) -> tuple[RiskDecision, dict[str, Any]] | None: ...


class BundleSource(Protocol):
    async def get_by_rule_hash(self, rule_hash: str) -> RiskRuleBundle | None: ...


@dataclass(frozen=True)
class ReplayResult:
    match: bool
    diff: dict[str, Any]


class DecisionNotFoundError(LookupError):
    """`decision_id`가 WORM 원장에 없다."""


class BundleNotFoundError(LookupError):
    """저장된 `rule_hash`에 매칭되는 rule_bundle이 없다 — 번들 삭제/오염 신호."""


async def replay(
    decision_repo: DecisionSource,
    bundle_repo: BundleSource,
    *,
    decision_id: UUID,
) -> ReplayResult:
    stored = await decision_repo.get(decision_id)
    if stored is None:
        raise DecisionNotFoundError(str(decision_id))
    decision, inputs_snapshot = stored

    bundle = await bundle_repo.get_by_rule_hash(decision.rule_hash)
    if bundle is None:
        raise BundleNotFoundError(decision.rule_hash)

    inputs = RiskInputs.model_validate(inputs_snapshot)
    ttl_seconds = (decision.expires_at - decision.evaluated_at).total_seconds()
    recomputed = evaluate(
        inputs,
        bundle,
        gate_kind=decision.gate_kind,
        trace_id=decision.trace_id,
        now=decision.evaluated_at,
        ttl=ttl_seconds,
    )

    diff: dict[str, Any] = {}
    for field in _COMPARED_FIELDS:
        stored_value = getattr(decision, field)
        recomputed_value = getattr(recomputed, field)
        if stored_value != recomputed_value:
            diff[field] = {"stored": stored_value, "recomputed": recomputed_value}

    match = not diff
    if not match:
        get_registry().counter(CORE_RISK_REPLAY_MISMATCH_COUNT_TOTAL).inc()

    return ReplayResult(match=match, diff=diff)


__all__ = [
    "ReplayResult",
    "DecisionSource",
    "BundleSource",
    "DecisionNotFoundError",
    "BundleNotFoundError",
    "INTEGRITY_RISK_REPLAY_MISMATCH",
    "replay",
]
