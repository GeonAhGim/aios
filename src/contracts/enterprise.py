"""Core enterprise contracts v1.

These objects carry a decision, evidence, strategy package, or order proposal
between bounded contexts.  They are deliberately transport-only: an
``OrderIntent`` cannot contain a broker credential or executable order detail.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContractEnvelope(_StrictContract):
    schema_version: Literal["1.0.0"]
    contract_id: UUID
    tenant_id: str = Field(min_length=1, max_length=128)
    trace_id: str = Field(min_length=16, max_length=128)
    correlation_id: UUID | None = None
    created_at: datetime
    classification: Literal["public", "internal", "confidential", "restricted"]


class PolicyReference(_StrictContract):
    policy_id: str
    version: str
    rule_id: str


class Obligation(_StrictContract):
    type: str
    parameters: dict[str, Any] | None = None


class PolicyDecision(ContractEnvelope):
    contract_type: Literal["PolicyDecision"]
    decision_id: UUID
    effect: Literal["ALLOW", "DENY", "REQUIRE_APPROVAL", "INDETERMINATE"]
    reason_codes: list[str] = Field(min_length=1)
    policy_refs: list[PolicyReference] = Field(min_length=1)
    obligations: list[Obligation] = Field(default_factory=list)
    permitted_scope: dict[str, Any] | None = None
    valid_from: datetime | None = None
    expires_at: datetime | None = None
    evaluated_at: datetime
    audit_event_id: UUID


class EvidenceSource(_StrictContract):
    provider: str
    source_ref: str
    license_ref: str | None = None


class EvidenceProvenance(_StrictContract):
    content_hash: str = Field(pattern=r"^sha256:[A-Fa-f0-9]{64}$")
    collected_at: datetime
    observed_at: datetime
    reproducibility: Literal["FULL", "PARTIAL", "NON_REPRODUCIBLE"]
    dataset_version: str | None = None
    transform_chain: list[str] = Field(default_factory=list)


class EvidenceIntegrity(_StrictContract):
    hash_algorithm: Literal["sha256"]
    digest: str = Field(pattern=r"^[A-Fa-f0-9]{64}$")
    signature_ref: str | None = None


class EvidenceRetention(_StrictContract):
    retention_class: str = Field(alias="class")
    expires_at: datetime | None = None
    legal_hold: bool = False


class EvidenceReference(ContractEnvelope):
    contract_type: Literal["EvidenceReference"]
    evidence_id: UUID
    kind: Literal[
        "DATASET", "DOCUMENT", "MARKET_EVENT", "MODEL_OUTPUT", "HUMAN_REVIEW", "TEST_RESULT"
    ]
    source: EvidenceSource
    provenance: EvidenceProvenance
    integrity: EvidenceIntegrity
    retention: EvidenceRetention
    access_policy_ref: str


class StrategyScope(_StrictContract):
    markets: list[str] = Field(min_length=1)
    asset_classes: list[str] = Field(min_length=1)
    time_horizons: list[str] = Field(min_length=1)
    instruments: list[str] = Field(default_factory=list)


class StrategyHypothesis(_StrictContract):
    statement: str = Field(min_length=1)
    evidence_refs: list[UUID] = Field(min_length=1)
    uncertainty: str


class StrategyDependencies(_StrictContract):
    data: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list, max_length=0)


class StrategyRiskEnvelope(_StrictContract):
    prohibited_actions: list[str] = Field(min_length=1)
    declared_constraints: dict[str, Any] = Field(default_factory=dict)


class StrategyArtifact(_StrictContract):
    artifact_ref: str
    content_hash: str = Field(pattern=r"^sha256:[A-Fa-f0-9]{64}$")
    build_environment_ref: str


class StrategyPackage(ContractEnvelope):
    contract_type: Literal["StrategyPackage"]
    strategy_id: UUID
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    lifecycle: Literal[
        "DRAFT",
        "REVIEWED",
        "BACKTESTED",
        "OOS_PASSED",
        "PAPER_ELIGIBLE",
        "LIVE_CANDIDATE",
        "RETIRED",
        "REVOKED",
    ]
    scope: StrategyScope
    hypothesis: StrategyHypothesis
    dependencies: StrategyDependencies
    risk_envelope: StrategyRiskEnvelope
    artifact: StrategyArtifact
    validation_refs: list[UUID] = Field(default_factory=list)
    license_ref: str


class StrategyReference(_StrictContract):
    strategy_id: UUID
    version: str
    artifact_hash: str = Field(pattern=r"^sha256:[A-Fa-f0-9]{64}$")


class InstrumentReference(_StrictContract):
    canonical_instrument_id: str
    asset_class: str
    venue_constraints: list[str] = Field(default_factory=list)


class OrderTarget(_StrictContract):
    target_exposure: str = Field(pattern=r"^-?[0-9]+(\.[0-9]+)?$")
    maximum_notional: str = Field(pattern=r"^[0-9]+(\.[0-9]+)?$")
    currency: str = Field(pattern=r"^[A-Z]{3,5}$")
    quantity_hint: str | None = None


class OrderIntent(ContractEnvelope):
    """A policy-governed proposal; never an executable broker request."""

    contract_type: Literal["OrderIntent"]
    intent_id: UUID
    portfolio_id: UUID
    strategy_ref: StrategyReference
    instrument: InstrumentReference
    direction: Literal["LONG", "SHORT", "REDUCE", "EXIT", "HEDGE"]
    target: OrderTarget
    horizon: str
    urgency: Literal["LOW", "NORMAL", "HIGH"]
    rationale_evidence_refs: list[UUID] = Field(min_length=1)
    policy_context_ref: UUID
    risk_context_ref: UUID
    idempotency_key: str = Field(min_length=16, max_length=256)
    expires_at: datetime
    status: Literal[
        "PROPOSED",
        "POLICY_DENIED",
        "RISK_DENIED",
        "APPROVAL_PENDING",
        "APPROVED_FOR_EXECUTION",
        "EXPIRED",
        "CANCELLED",
        "RECONCILIATION_REQUIRED",
    ]
