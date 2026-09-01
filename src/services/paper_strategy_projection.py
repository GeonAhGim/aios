"""Paper-only projection from the legacy strategy builder to enterprise contracts.

The existing strategy table predates the tenant, evidence, and artifact fields
required by ``StrategyPackage``.  This adapter therefore never guesses those
values and never promotes an existing APPROVED or DEPLOYED strategy.  It is a
transport boundary for a strategy that has reached ``PAPER_TRADING`` only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import StrategyPackage
from src.contracts.enterprise import (
    StrategyArtifact,
    StrategyDependencies,
    StrategyHypothesis,
    StrategyRiskEnvelope,
    StrategyScope,
)
from src.services.strategy_builder_service import StrategyDetail, StrategyLifecycleError


class _StrictProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PaperStrategyProjectionInput(_StrictProjection):
    """Explicit governance metadata required to project one saved strategy.

    The caller supplies the enterprise identity and evidence intentionally:
    neither can be derived safely from the legacy strategy record.
    """

    contract_id: UUID
    contract_strategy_id: UUID
    tenant_id: str = Field(min_length=1, max_length=128)
    trace_id: str = Field(min_length=16, max_length=128)
    correlation_id: UUID | None = None
    created_at: datetime
    classification: Literal["public", "internal", "confidential", "restricted"] = "confidential"
    markets: list[str] = Field(min_length=1)
    asset_classes: list[str] = Field(min_length=1)
    time_horizons: list[str] = Field(min_length=1)
    instruments: list[str] = Field(default_factory=list)
    hypothesis_statement: str = Field(min_length=1)
    evidence_refs: list[UUID] = Field(min_length=1)
    uncertainty: str = Field(min_length=1)
    data_dependencies: list[str] = Field(default_factory=list)
    feature_dependencies: list[str] = Field(default_factory=list)
    model_dependencies: list[str] = Field(default_factory=list)
    declared_constraints: dict[str, object] = Field(default_factory=dict)
    artifact_ref: str = Field(min_length=1)
    artifact_hash: str = Field(pattern=r"^sha256:[A-Fa-f0-9]{64}$")
    build_environment_ref: str = Field(min_length=1)
    validation_refs: list[UUID] = Field(default_factory=list)
    license_ref: str = Field(min_length=1)


def project_paper_eligible_strategy(
    strategy: StrategyDetail, projection: PaperStrategyProjectionInput
) -> StrategyPackage:
    """Create a non-executable enterprise package for a paper-trading strategy.

    This function intentionally has no execution, broker, credential, payment,
    or persistence dependency.  A later live-promotion workflow must be a
    separate, policy-governed capability.
    """

    if strategy.lifecycle_status != "PAPER_TRADING":
        raise StrategyLifecycleError(
            "엔터프라이즈 paper package는 PAPER_TRADING 전략만 투영할 수 있습니다. "
            "LIVE 승격은 별도 정책·승인 워크플로가 필요합니다."
        )

    return StrategyPackage(
        schema_version="1.0.0",
        contract_id=projection.contract_id,
        tenant_id=projection.tenant_id,
        trace_id=projection.trace_id,
        correlation_id=projection.correlation_id,
        created_at=projection.created_at,
        classification=projection.classification,
        contract_type="StrategyPackage",
        strategy_id=projection.contract_strategy_id,
        version=strategy.version,
        lifecycle="PAPER_ELIGIBLE",
        scope=StrategyScope(
            markets=projection.markets,
            asset_classes=projection.asset_classes,
            time_horizons=projection.time_horizons,
            instruments=projection.instruments,
        ),
        hypothesis=StrategyHypothesis(
            statement=projection.hypothesis_statement,
            evidence_refs=projection.evidence_refs,
            uncertainty=projection.uncertainty,
        ),
        dependencies=StrategyDependencies(
            data=projection.data_dependencies,
            features=projection.feature_dependencies,
            models=projection.model_dependencies,
            capabilities=[],
        ),
        risk_envelope=StrategyRiskEnvelope(
            prohibited_actions=["direct_broker_access", "live_order_submission"],
            declared_constraints=projection.declared_constraints,
        ),
        artifact=StrategyArtifact(
            artifact_ref=projection.artifact_ref,
            content_hash=projection.artifact_hash,
            build_environment_ref=projection.build_environment_ref,
        ),
        validation_refs=projection.validation_refs,
        license_ref=projection.license_ref,
    )
