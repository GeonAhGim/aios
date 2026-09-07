"""Portfolio Mandate 도메인 모델 — pure value object.

Spec: AIOSproject 75_portfolio_mandate_l3_build_and_operational_specification_v1.0.md §1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import UUID


class MandateRevisionState(str, Enum):
    DRAFT = "DRAFT"
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"


class Autonomy(str, Enum):
    """75번 §3 ALLOWED_AUTONOMY 규칙이 참조하는 3단계. LIMITED_LIVE라고 해서
    이 코드베이스가 실제로 LIVE를 허용한다는 뜻은 아니다 — mandate가 표현하는
    "사용자가 위임하고 싶은 상한"일 뿐이고, 실제 LIVE 게이트는 여전히 별도
    FROZEN 영역(executor.py)이 막는다(30/32번 문서 원칙)."""

    OBSERVE = "OBSERVE"
    PAPER = "PAPER"
    LIMITED_LIVE = "LIMITED_LIVE"


@dataclass(frozen=True)
class MandateRevision:
    """75번 §3 컴파일러가 참조하는 6개 규칙(MAX_TOTAL_EXPOSURE 등)만 필드로
    갖는다 — 45번 §1의 8-section 전체가 아니라 지금 실제로 evaluate_policy()가
    쓸 수 있는 것만(§2 스콥 축소, 마이그레이션 docstring 참조)."""

    id: UUID
    mandate_id: UUID
    revision_no: int
    state: MandateRevisionState
    max_total_exposure_pct: float
    max_single_instrument_pct: float
    min_cash_buffer_pct: float
    max_daily_loss_pct: float
    allowed_autonomy: Autonomy
    forbidden_assets: tuple[str, ...] = field(default_factory=tuple)
    revision_hash: str = ""
    cooling_off_started_at: datetime | None = None
    created_at: datetime | None = None
    activated_at: datetime | None = None
    # L4_compliance_and_regulatory_v1.0.md §9 CM-2 — 7종 제약 중 이 리프가
    # 새로 추가하는 5종(자산군·국가·통화·유동성·ESG 배제). 집중도는 기존
    # max_single_instrument_pct, 레버리지는 아래 max_leverage_ratio가 맡는다.
    # 전부 기본값을 둬 기존 DB 컬럼(마이그레이션 없음)·기존 호출부와 호환된다.
    excluded_asset_classes: tuple[str, ...] = field(default_factory=tuple)
    excluded_countries: tuple[str, ...] = field(default_factory=tuple)
    excluded_currencies: tuple[str, ...] = field(default_factory=tuple)
    min_liquidity_score: float | None = None
    esg_excluded_symbols: tuple[str, ...] = field(default_factory=tuple)
    max_leverage_ratio: float | None = None


@dataclass(frozen=True)
class PortfolioMandate:
    id: UUID
    tenant_id: UUID
    subject_id: UUID
    portfolio_id: UUID
    active_revision_id: UUID | None
    created_at: datetime


class PolicyOutcome(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    REQUIRE_REASSESSMENT = "REQUIRE_REASSESSMENT"
    PAUSE_REQUIRED = "PAUSE_REQUIRED"


@dataclass(frozen=True)
class PolicyBundle:
    """PolicyCompiler.compile()의 순수 산출물 — mandate_revision 하나당 정확히
    하나(75번 §1 UNIQUE (mandate_revision_id))."""

    id: UUID
    mandate_revision_id: UUID
    compiler_version: str
    rule_hash: str
    created_at: datetime


@dataclass(frozen=True)
class PolicyEvaluationSubject:
    """EvaluatePolicy의 입력 — "임의 JSON이 아니라 타입이 있는 입력"(75번 §3).
    실제 주문 크기가 아니라, 이 leaf 스콥에서 policy가 판단할 수 있는 최소
    필드만 담는다(전략/실행 계층은 아직 이 계약을 소비하지 않음 — FND-02는
    mandate/policy 자체를 만드는 리프이지 배선하는 리프가 아니다)."""

    command_type: str
    instrument_exposure_pct: float | None = None
    total_exposure_pct: float | None = None
    cash_buffer_pct: float | None = None
    projected_daily_loss_pct: float | None = None
    requested_autonomy: Autonomy | None = None
    asset: str | None = None
    # CM-2 §9 — 7종 제약 평가에 필요한 나머지 입력. `asset`은 forbidden_assets와
    # esg_excluded_symbols 양쪽에 재사용한다(둘 다 "이 심볼이 목록에 있는가"라
    # 별도 필드가 필요 없다).
    asset_class: str | None = None
    country: str | None = None
    currency: str | None = None
    liquidity_score: float | None = None
    leverage_ratio: float | None = None


@dataclass(frozen=True)
class PolicyDecision:
    id: UUID
    tenant_id: UUID
    bundle_id: UUID
    command_type: str
    command_fingerprint: str
    outcome: PolicyOutcome
    reason_codes: tuple[str, ...]
    obligations: tuple[str, ...]
    evaluated_at: datetime
    expires_at: datetime | None
