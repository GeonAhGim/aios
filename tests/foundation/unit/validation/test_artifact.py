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


def test_verify_detects_tampered_compiler_version():
    """A forged `compiler_version` (claiming a different compiler build
    produced this artifact) must be caught the same way as any other
    hash-input field."""
    artifact = _artifact()
    tampered = artifact.model_copy(update={"compiler_version": "cc-test-forged"})
    assert verify(tampered) == ARTIFACT_HASH_MISMATCH


def test_verify_detects_tampered_grammar_version():
    """A forged `grammar_version` (claiming the artifact was compiled
    against a different DSL grammar than it actually was) must be caught
    the same way as any other hash-input field."""
    artifact = _artifact()
    tampered = artifact.model_copy(update={"grammar_version": "grammar-forged"})
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
# D2 negative -- 빈/부정 입력은 ValidationError로 즉시 거부해야 한다.
# content-addressed 해시(I-04)는 유효한 입력에서만 계산되므로,
# invalid input이 model을 통과해 artifact_hash를 계산하는 것을 막는다.
# --------------------------------------------------------------------------


def test_negative_none_strategy_id():
    """strategy_id에 None을 전달하면 Pydantic ValidationError가 발생해야 한다.
    불변식: artifact의 모든 str 필드는 None이 아닌 문자열 값만 허용된다
    (I-04 content-addressed, fail-closed).
    """
    with pytest.raises(ValidationError) as ctx:
        build_artifact(
            strategy_id=None,
            version="v1",
            fsm_definition=FSM_DEFINITION,
            compiler_version="cc-test-1",
        )
    assert "strategy_id" in str(ctx.value)


def test_negative_none_version():
    """version에 None을 전달하면 Pydantic ValidationError가 발생해야 한다.
    불변식: 버전 필드는 None이 아닌 문자열 값만 허용된다 (I-04).
    """
    with pytest.raises(ValidationError) as ctx:
        build_artifact(
            strategy_id="strat-1",
            version=None,
            fsm_definition=FSM_DEFINITION,
            compiler_version="cc-test-1",
        )
    assert "version" in str(ctx.value)


def test_negative_integer_version():
    """version에 정수(예: 1)를 전달하면 Pydantic ValidationError가 발생해야 한다.
    불변식: 버전 필드는 str 타입만 허용된다 — 정수/float/byte는 거부된다.
    """
    with pytest.raises(ValidationError) as ctx:
        build_artifact(
            strategy_id="strat-1",
            version=1,
            fsm_definition=FSM_DEFINITION,
            compiler_version="cc-test-1",
        )
    assert "version" in str(ctx.value)


# --------------------------------------------------------------------------
# D2 실패주입 -- L02 레지스트리(의존성) 예외는 fail-closed로 전파돼야 한다.
# artifact_hash가 잘못된 registry_version(예: 빈 문자열)으로 조용히 만들어지면
# I-04(content-addressed, immutable) 위반이므로, 여기서 삼켜서 성공으로
# 위장하지 않는지 확인한다.
# --------------------------------------------------------------------------


def test_build_artifact_propagates_registry_hash_failure(monkeypatch):
    def _boom() -> str:
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(DEFAULT_REGISTRY, "registry_hash", _boom)
    with pytest.raises(RuntimeError, match="registry unavailable"):
        _artifact()


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
