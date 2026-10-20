"""Unit tests for `src/foundation/ai/providers/domain/prompt_registry.py` --
task-2639 AI-5 ("재현 키 구성 요소")."""

from __future__ import annotations

import pytest

from src.foundation.ai.providers.domain.prompt_registry import (
    DuplicatePromptVersionError,
    PromptRegistry,
    PromptRegistryError,
    PromptTemplate,
    PromptVersionNotFoundError,
    prompt_hash,
)
from tests.conftest import PerfBudget


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


@pytest.mark.perf
def test_prompt_hash_p95_latency_within_self_declared_budget(
    perf_budget: PerfBudget,
) -> None:
    """task-10940: process_time 기반 perf_budget으로 측정해 부하 민감(워커
    다수·로컬 LLM 동시 실행) 호스트에서 다른 프로세스에 코어를 뺏겨 대기한
    시간이 wall-clock에 섞이지 않게 한다. Windows process_time 해상도(~15.6ms)
    양자화 오차를 줄이기 위해 batch=8을 묶는다(task-7360과 동일 수법)."""
    template = _template(template="x" * 2000)
    samples = perf_budget.samples(lambda: prompt_hash(template), n=200, batch=8)
    cpu_values_ms = sorted(s.cpu_ms for s in samples)
    p95_ms = cpu_values_ms[min(int(len(cpu_values_ms) * 0.95), len(cpu_values_ms) - 1)]
    print(
        f"[AI-5 prompt_registry] prompt_hash p95={p95_ms:.4f}ms "
        f"budget<{_PROMPT_HASH_BUDGET_MS:.1f}ms"
    )
    assert p95_ms < _PROMPT_HASH_BUDGET_MS


# ---- failure-injection: CPU 시간 10배 주입 → 단언이 적색으로 전환됨 ----
# PerfBudget.sample 는 time.process_time() 을 사용하므로, sleep() 주입은
# CPU 측정에 보이지 않는다. 대신 time.process_time 를 monkeypatch 해 실제
# CPU 시간의 10 배를 반환하게 하면预算 단언이 반드시 실패해야 한다.


@pytest.mark.perf
def test_prompt_hash_budget_assertion_fails_on_cpu_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """정상 입력이预算을 통과하는 측정 경로를, CPU 시간을 10 배로 늘리면
    적색으로 전환한다 — 성능 게이트가 지연 회귀를 실제로 적색으로
    포착한다는 것을 단언 수준에서 증명 (D2 DoD 실패주입 1 건).

    `PerfBudget` 은 `time.process_time()` 을 쓰므로, `sleep()` 주입은
    CPU 시간 측정에 보이지 않는다. `PerfBudget` 이 `time` 모듈을
    import 하므로, `tests.support.perf_budget.time.process_time` 을
    monkeypatch 해 실제 CPU 시간의 10 배를 반환하게 한다.

    prompt_hash 는 실제 CPU 시간이 ~0ms 로 너무 작아 10 배 곱셈으로도
    Windows process_time 양자화(~15.6ms tick) 를 넘지 못한다.
    대신 실제 측정값에 5ms 고정 지연을 더하면 original_budget(1.0ms) 을
    명확히 초과하므로, budget=1.0ms 단언이 반드시 적색으로 전환된다.
    """
    import tests.support.perf_budget as _perf_budget_mod

    template = _template(template="x" * 2000)

    original_process_time = _perf_budget_mod.time.process_time
    # call counter — 매 호출마다 process_time 반환값에 누적 지연 추가
    _call_count = [0]

    def _slow_process_time() -> float:
        _call_count[0] += 1
        # 각 호출마다 0.01초(10ms) 씩 누적 — 1회 측정당 2회 호출이므로
        # 20ms 누적 → cpu_ms = 20 * 1000 / 8 = 2500ms >> budget(1.0ms).
        return original_process_time() + _call_count[0] * 0.01

    monkeypatch.setattr(_perf_budget_mod.time, "process_time", _slow_process_time)

    try:
        budget_ms = 1.0
        samples = perf_budget.samples(lambda: prompt_hash(template), n=200, batch=8)
        cpu_values_ms = sorted(s.cpu_ms for s in samples)
        p95_ms = cpu_values_ms[min(int(len(cpu_values_ms) * 0.95), len(cpu_values_ms) - 1)]
        print(
            f"[AI-5 prompt_registry] prompt_hash p95={p95_ms:.4f}ms "
            f"budget<{budget_ms:.1f}ms (injected)"
        )
        assert p95_ms < budget_ms, (
            f"주입된 CPU 시간이预算을 초과해야 함 — p95={p95_ms:.4f}ms budget={budget_ms:.1f}ms"
        )
        pytest.fail(
            f"assert_within 이 적색을 raised 하지 않음 — "
            f"p95={p95_ms:.4f}ms budget={budget_ms:.1f}ms "
            f"주입이 감지되지 않음"
        )
    except AssertionError as exc:
        # 적색이 감지됨 — 기대 동작
        assert "budget" in str(exc) or "p95" in str(exc)
    finally:
        _perf_budget_mod.time.process_time = original_process_time
