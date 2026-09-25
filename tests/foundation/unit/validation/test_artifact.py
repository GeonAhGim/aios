"""L36 -- unit tests for `StrategyArtifact` content-addressing and tamper
detection (§2 row 158). Pure domain, no DB. Includes D2 evidence
(ADR-2026-09-09-C Decision 1): negative + perf assertion (the leaf's
failure-injection and gate-red repro live in `test_policy_hash.py`).
"""

import time

import pytest
from pydantic import ValidationError

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


def test_verify_detects_tampered_registry_version():
    """A hand-edited `registry_version` (e.g. forging an older/newer L02
    registry state without actually rebuilding against it) must be caught
    the same way as a tampered `fsm_definition` -- all four hash inputs are
    equally load-bearing, not just the FSM body."""
    artifact = _artifact()
    tampered = artifact.model_copy(update={"registry_version": "forged-registry-hash"})
    assert verify(tampered) == ARTIFACT_HASH_MISMATCH


# --------------------------------------------------------------------------
# D2 negative -- frozen 모델은 필드 하나만 바뀌어도 새 artifact_hash를 요구한다
# (§2 row 158 "content-addressed and immutable once versioned").
# --------------------------------------------------------------------------


def test_artifact_is_frozen_rejects_mutation():
    artifact = _artifact()
    with pytest.raises(ValidationError):
        artifact.strategy_id = "strat-2"


# --------------------------------------------------------------------------
# D2 성능 단언 -- ADR-2026-09-09-C 예산표 "DSL 컴파일 300ms"를 이 leaf에 적용.
# build_artifact()는 컴파일 파이프라인의 마지막 단계(해시 고정)이므로, 500
# state/2,000 transition 규모 FSM에서도 그 예산의 여유 안에서 끝남을 확인한다
# (canonical_json 정규화 병리적 회귀 방지).
# --------------------------------------------------------------------------


@pytest.mark.perf
def test_build_artifact_large_fsm_within_dsl_compile_budget():
    large_fsm = {
        "states": [f"S{i}" for i in range(500)],
        "transitions": [
            {
                "from": f"S{i % 500}",
                "to": f"S{(i + 1) % 500}",
                "condition": f"rsi_14 < {i % 100}",
            }
            for i in range(2000)
        ],
    }
    start = time.perf_counter()
    _artifact(fsm_definition=large_fsm)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.3, f"large-FSM build_artifact() took {elapsed * 1000:.2f}ms, budget 300ms"
