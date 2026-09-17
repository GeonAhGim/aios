"""Unit tests for `src/foundation/ai/providers/domain/prompt_registry.py` --
task-2639 AI-5 ("재현 키 구성 요소")."""

from __future__ import annotations

import time

import pytest

from src.foundation.ai.providers.domain.prompt_registry import (
    DuplicatePromptVersionError,
    PromptRegistry,
    PromptRegistryError,
    PromptTemplate,
    PromptVersionNotFoundError,
    prompt_hash,
)


def _template(
    *, prompt_id: str = "strategy.propose", version: int = 1, template: str = "generate a proposal"
) -> PromptTemplate:
    return PromptTemplate(prompt_id=prompt_id, version=version, template=template)


# --- 정상 경로 ---


def test_register_then_get_round_trips() -> None:
    registry = PromptRegistry.empty().register(_template())
    fetched = registry.get("strategy.propose", 1)
    assert fetched.template == "generate a proposal"


def test_register_returns_new_registry_original_unaffected() -> None:
    empty = PromptRegistry.empty()
    populated = empty.register(_template())
    assert empty.entries == {}
    assert ("strategy.propose", 1) in populated.entries


def test_latest_version_picks_max_across_versions() -> None:
    registry = (
        PromptRegistry.empty()
        .register(_template(version=1))
        .register(_template(version=3))
        .register(_template(version=2))
    )
    assert registry.latest_version("strategy.propose") == 3


# --- 재현 키: 결정론 해시 ---


def test_prompt_hash_is_deterministic_across_calls() -> None:
    template = _template()
    assert prompt_hash(template) == prompt_hash(template)


def test_prompt_hash_changes_when_template_text_changes() -> None:
    """프롬프트 본문이 재현 키에 실제로 반영되지 않으면 서로 다른 프롬프트가
    같은 재현 키를 만들어낼 수 있다 -- 재현성(§1)이 깨진다."""
    a = _template(template="version A")
    b = _template(template="version B")
    assert prompt_hash(a) != prompt_hash(b)


def test_prompt_hash_changes_when_version_changes() -> None:
    a = _template(version=1)
    b = _template(version=2)
    assert prompt_hash(a) != prompt_hash(b)


def test_prompt_hash_is_lowercase_sha256_hex() -> None:
    digest = prompt_hash(_template())
    assert len(digest) == 64
    assert digest == digest.lower()
    int(digest, 16)  # ValueError면 hex가 아니다


# --- 부정 테스트: 중복 등록, 미등록 조회, 잘못된 생성 (>=3) ---


def test_register_rejects_duplicate_prompt_id_and_version() -> None:
    """§2.2 "(prompt_id, version)은 등록 후 불변" -- 같은 키를 다시 등록하면
    재현 키가 등록 순서에 따라 다른 프롬프트 본문을 가리킬 수 있으므로 거부."""
    registry = PromptRegistry.empty().register(_template())
    with pytest.raises(DuplicatePromptVersionError):
        registry.register(_template(template="a different body now"))


def test_get_rejects_unregistered_prompt_id() -> None:
    registry = PromptRegistry.empty().register(_template())
    with pytest.raises(PromptVersionNotFoundError):
        registry.get("unknown.prompt", 1)


def test_get_rejects_unregistered_version_of_known_prompt() -> None:
    registry = PromptRegistry.empty().register(_template(version=1))
    with pytest.raises(PromptVersionNotFoundError):
        registry.get("strategy.propose", 2)


def test_latest_version_rejects_prompt_id_with_no_registrations() -> None:
    with pytest.raises(PromptVersionNotFoundError):
        PromptRegistry.empty().latest_version("nothing.registered")


def test_prompt_template_rejects_empty_prompt_id() -> None:
    with pytest.raises(PromptRegistryError):
        _template(prompt_id="")


def test_prompt_template_rejects_non_positive_version() -> None:
    with pytest.raises(PromptRegistryError):
        _template(version=0)


def test_prompt_template_rejects_empty_template_body() -> None:
    with pytest.raises(PromptRegistryError):
        _template(template="")


# --- 실패 주입: 상류 손상이 조용히 통과하지 않고 fail-closed 거부 ---


def test_prompt_hash_rejects_non_string_template_body_from_upstream_corruption() -> None:
    """실패 주입: 상류(저장소 역직렬화)가 손상되어 `template`이 문자열이
    아니라 바이트열로 섞여 들어오면(frozen dataclass라 __post_init__ 이후
    직접 대입은 object.__setattr__로만 가능 -- 역직렬화 손상을 흉내낸다),
    canonical_json은 알 수 없는 타입을 조용히 문자열화하지 않고
    TypeError를 내야 한다(json.dumps가 bytes를 직렬화할 수 없다)."""
    template = _template()
    object.__setattr__(template, "template", b"corrupted-bytes-not-str")
    with pytest.raises(TypeError):
        prompt_hash(template)


# --- 수치 성능 단언 ---

_PROMPT_HASH_BUDGET_MS = 1.0


def test_prompt_hash_p95_latency_within_self_declared_budget() -> None:
    template = _template(template="x" * 2000)
    samples: list[float] = []
    for _ in range(200):
        started = time.perf_counter()
        prompt_hash(template)
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    p95_ms = samples[min(int(len(samples) * 0.95), len(samples) - 1)]
    print(
        f"[AI-5 prompt_registry] prompt_hash p95={p95_ms:.4f}ms "
        f"budget<{_PROMPT_HASH_BUDGET_MS:.1f}ms"
    )
    assert p95_ms < _PROMPT_HASH_BUDGET_MS
