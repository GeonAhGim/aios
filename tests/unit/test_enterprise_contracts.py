from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from src.contracts import EvidenceReference, OrderIntent, PolicyDecision, StrategyPackage

_UUIDS = {
    "contract": "11111111-1111-4111-8111-111111111111",
    "decision": "11111111-1111-4111-8111-111111111112",
    "audit": "11111111-1111-4111-8111-111111111113",
    "evidence": "22222222-2222-4222-8222-222222222222",
    "strategy": "33333333-3333-4333-8333-333333333332",
    "intent": "44444444-4444-4444-8444-444444444442",
    "portfolio": "44444444-4444-4444-8444-444444444443",
    "risk": "44444444-4444-4444-8444-444444444444",
}
_HASH = "a" * 64
_ENVELOPE = {
    "schema_version": "1.0.0",
    "contract_id": _UUIDS["contract"],
    "tenant_id": "tenant-demo",
    "trace_id": "0123456789abcdef",
    "created_at": "2026-08-30T00:00:00Z",
    "classification": "confidential",
}


def test_policy_decision_accepts_governed_decision():
    decision = PolicyDecision.model_validate(
        {
            **_ENVELOPE,
            "contract_type": "PolicyDecision",
            "decision_id": _UUIDS["decision"],
            "effect": "ALLOW",
            "reason_codes": ["POLICY_MATCH"],
            "policy_refs": [{"policy_id": "paper", "version": "1.0.0", "rule_id": "only"}],
            "evaluated_at": "2026-08-30T00:00:00Z",
            "audit_event_id": _UUIDS["audit"],
        }
    )
    assert decision.effect == "ALLOW"


def test_evidence_reference_requires_integrity_and_provenance():
    evidence = EvidenceReference.model_validate(
        {
            **_ENVELOPE,
            "contract_type": "EvidenceReference",
            "evidence_id": _UUIDS["evidence"],
            "kind": "DATASET",
            "source": {"provider": "approved", "source_ref": "dataset:1"},
            "provenance": {
                "content_hash": f"sha256:{_HASH}",
                "collected_at": "2026-08-30T00:00:00Z",
                "observed_at": "2026-08-30T00:00:00Z",
                "reproducibility": "FULL",
            },
            "integrity": {"hash_algorithm": "sha256", "digest": _HASH},
            "retention": {"class": "research"},
            "access_policy_ref": "policy:internal",
        }
    )
    assert evidence.provenance.reproducibility == "FULL"


def test_strategy_package_must_be_paper_safe_when_no_capability_is_declared():
    package = StrategyPackage.model_validate(
        {
            **_ENVELOPE,
            "contract_type": "StrategyPackage",
            "strategy_id": _UUIDS["strategy"],
            "version": "1.0.0",
            "lifecycle": "PAPER_ELIGIBLE",
            "scope": {
                "markets": ["kr_equity"],
                "asset_classes": ["equity"],
                "time_horizons": ["medium"],
            },
            "hypothesis": {
                "statement": "illustrative",
                "evidence_refs": [_UUIDS["evidence"]],
                "uncertainty": "not live validated",
            },
            "dependencies": {"data": [], "features": [], "models": [], "capabilities": []},
            "risk_envelope": {"prohibited_actions": ["direct_broker_access"]},
            "artifact": {
                "artifact_ref": "artifact:demo",
                "content_hash": f"sha256:{_HASH}",
                "build_environment_ref": "build:1",
            },
            "license_ref": "license:internal",
        }
    )
    assert package.lifecycle == "PAPER_ELIGIBLE"


def test_order_intent_rejects_direct_broker_credentials():
    payload = {
        **_ENVELOPE,
        "contract_type": "OrderIntent",
        "intent_id": _UUIDS["intent"],
        "portfolio_id": _UUIDS["portfolio"],
        "strategy_ref": {
            "strategy_id": _UUIDS["strategy"],
            "version": "1.0.0",
            "artifact_hash": f"sha256:{_HASH}",
        },
        "instrument": {"canonical_instrument_id": "KRX:005930", "asset_class": "equity"},
        "direction": "LONG",
        "target": {"target_exposure": "0.05", "maximum_notional": "1000000", "currency": "KRW"},
        "horizon": "medium",
        "urgency": "NORMAL",
        "rationale_evidence_refs": [_UUIDS["evidence"]],
        "policy_context_ref": _UUIDS["decision"],
        "risk_context_ref": _UUIDS["risk"],
        "idempotency_key": "intent-demo-key-0001",
        "expires_at": "2026-08-30T01:00:00Z",
        "status": "PROPOSED",
    }
    assert OrderIntent.model_validate(payload).status == "PROPOSED"

    forbidden = deepcopy(payload)
    forbidden["broker_api_key"] = "must-not-be-accepted"
    with pytest.raises(ValidationError):
        OrderIntent.model_validate(forbidden)
