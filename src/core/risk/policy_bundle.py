"""L4_risk_and_safety_v1.0.md#9 R-15 — `RiskRuleBundle` 모델·상태 + `compute_rule_hash`.

R10(정책 버전·인간 승인) 요구사항의 순수 코어: `risk_policy.yaml`을 파싱한
`RiskPolicy`와 엔진 버전으로부터 결정론적 rule hash를 뽑아, DB에 발행된
ACTIVE 번들의 `rule_hash`와 비교(엔진이 불일치 시 DENY)할 수 있게 한다.
해시 정규화는 R-01 `hashing.canonical_json`을 그대로 재사용한다 — Decimal
표현·키 순서 무관 성질을 여기서 다시 구현하지 않는다.

상태·전이는 78번 §1 `risk_rule_bundle` 테이블과 L4_risk_and_safety#4.3의
`DRAFT → APPROVED → ACTIVE → RETIRED` 선형 전이를 그대로 반영한다. 이
모듈은 전이 적법성만 순수하게 판정하고(`is_valid_transition`), 실제
`conditional_update`(예상 state 비교 후 UPDATE) 실행은 R-22
`adapters/postgres_bundle_repository.py`의 책임이다 — partial unique
`ux_bundle_active`(scope당 ACTIVE 최대 1개)와 WORM 트리거는 DB 레벨
제약이라 여기서는 표현하지 않는다(모순 없음: 이 모델은 스코프 간
비교를 하지 않으므로 partial unique 전제를 침해하지 않는다).
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, field_validator, model_validator

from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.risk.hashing import canonical_json, sha256_hex


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive datetime은 허용하지 않는다 — tz-aware UTC만 사용한다")
    return value


class BundleState(str, Enum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


# 78번 §1 / L4_risk_and_safety#4.3의 선형 전이. RETIRED는 종단 상태.
_ALLOWED_TRANSITIONS: dict[BundleState, frozenset[BundleState]] = {
    BundleState.DRAFT: frozenset({BundleState.APPROVED}),
    BundleState.APPROVED: frozenset({BundleState.ACTIVE}),
    BundleState.ACTIVE: frozenset({BundleState.RETIRED}),
    BundleState.RETIRED: frozenset(),
}


def is_valid_transition(current: BundleState, target: BundleState) -> bool:
    """R-22 `conditional_update(expected_state=current)` 호출 전에 적법성만
    미리 걸러내는 순수 판정 — 실제 원자적 전이는 여기서 하지 않는다."""
    return target in _ALLOWED_TRANSITIONS[current]


def compute_rule_hash(policy: RiskPolicy, engine_version: str) -> str:
    """`RiskPolicy` 파싱 결과(YAML 주석·키 순서 무관) + 엔진 버전의 sha256.

    `mode="python"`으로 덤프해 `Decimal`/원시 타입을 유지한 채
    `canonical_json`의 정규화(키 정렬)에 맡긴다 — `mode="json"`으로 미리
    문자열화하면 정규화가 무력화된다(R-01 `RiskInputs.inputs_hash`와 동일 이유).
    """
    payload = {
        "policy": policy.model_dump(mode="python"),
        "engine_version": engine_version,
    }
    return sha256_hex(canonical_json(payload))


class RiskRuleBundle(BaseModel, frozen=True):
    """78번 §1 `risk_rule_bundle` 테이블 1:1 순수 값 객체."""

    id: UUID
    scope: str = "GLOBAL"
    version: str
    rule_hash: str
    engine_version: str
    policy_snapshot: Mapping[str, Any]
    state: BundleState
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    created_by: UUID
    approved_by: UUID | None = None
    approval_ref: str | None = None
    approved_at: datetime | None = None
    activated_at: datetime | None = None
    retired_at: datetime | None = None

    @field_validator("rule_hash")
    @classmethod
    def _validate_rule_hash(cls, value: str) -> str:
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("rule_hash는 소문자 hex sha256(64자)여야 한다")
        return value

    @field_validator(
        "effective_from", "effective_to", "approved_at", "activated_at", "retired_at"
    )
    @classmethod
    def _validate_aware_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def _approved_by_required_unless_draft(self) -> RiskRuleBundle:
        # CHECK approved_by IS NOT NULL OR state IN ('DRAFT') — DRAFT를 벗어난
        # 번들은 승인자 없이 존재할 수 없다(fail-closed, I-09).
        if self.state != BundleState.DRAFT and self.approved_by is None:
            raise ValueError("DRAFT가 아닌 상태는 approved_by가 필수다")
        return self
