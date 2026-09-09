"""L36 -- unit tests for `StrategyArtifact` content-addressing and tamper
detection (§2 row 158). Pure domain, no DB.
"""
from src.core.indicators.registry import DEFAULT_REGISTRY
from src.foundation.validation.domain.artifact import (
    ARTIFACT_HASH_MISMATCH,
    build_artifact,
    verify,
)

FSM_DEFINITION = {
    "states": ["IDLE", "IN_POSITION"],
    "transitions": [{"from": "IDLE", "to": "IN_POSITION", "condition": "rsi_14 < 30"}],
}


def _artifact(**overrides):
    kwargs = dict(
        strategy_id="strat-1",
        version="v1",
        fsm_definition=FSM_DEFINITION,
        compiler_version="cc-test-1",
    )
    kwargs.update(overrides)
    return build_artifact(**kwargs)


def test_artifact_hash_is_stable_for_identical_input():
    a = _artifact()
    b = _artifact()
    assert a.artifact_hash == b.artifact_hash


def test_artifact_hash_changes_when_fsm_threshold_changes():
    baseline = _artifact()
    changed_fsm = {
        "states": ["IDLE", "IN_POSITION"],
        "transitions": [{"from": "IDLE", "to": "IN_POSITION", "condition": "rsi_14 < 25"}],
    }
    changed = _artifact(fsm_definition=changed_fsm)
    assert baseline.artifact_hash != changed.artifact_hash


def test_artifact_registry_version_delegates_to_l02_registry_hash():
    artifact = _artifact()
    assert artifact.registry_version == DEFAULT_REGISTRY.registry_hash()


def test_verify_returns_none_for_untampered_artifact():
    artifact = _artifact()
    assert verify(artifact) is None


def test_verify_detects_tampered_fsm_definition():
    artifact = _artifact()
    tampered_fsm = dict(artifact.fsm_definition)
    tampered_fsm["transitions"] = [
        {"from": "IDLE", "to": "IN_POSITION", "condition": "rsi_14 < 99"}
    ]
    tampered = artifact.model_copy(update={"fsm_definition": tampered_fsm})
    assert verify(tampered) == ARTIFACT_HASH_MISMATCH
