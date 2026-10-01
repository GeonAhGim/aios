"""U-3a -- 컴파일 통과율 회귀 픽스처.

Spec DoD: "생성된 스크립트 100%가 DSL 컴파일·리소스 상한 검사를 통과하거나
실패 사유를 사용자에게 명시(무음 실패 0건, 회귀 CI 픽스처)". 아래 픽스처는
provider가 낼 법한 대표 산출물(정상/구문오류/미래참조/자원상한초과) 각각을
`generate_script`에 통과시켜 매번 명시적 성공 또는 명시적 실패만 나오는지
집계한다.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from src.core.indicators.registry import DEFAULT_REGISTRY
from src.foundation.ai.assistant.application.compile_pass_rate import build_pass_rate_report
from src.foundation.ai.assistant.application.generate_script import generate_script
from src.foundation.ai.assistant.ports.script_provider import ScriptDraft
from tests.unit.foundation.ai.assistant.test_generate_script import NOW, InMemoryCounter

REGISTRY_VERSION = DEFAULT_REGISTRY.registry_hash()

_VALID_FIXTURE_SOURCE = (
    "input length: int = 14\n"
    "input close: series<float> = 0\n"
    "let r = ta.rsi(close, length)\n"
    "signal go_long = r < 30\n"
    "plot(r, 1)\n"
)

FIXTURE_SOURCES = [
    # 정상
    _VALID_FIXTURE_SOURCE,
    # 구문 오류
    "let a = 1 +",
    # 타입 오류
    "input a: int = 1\nlet bad = zz",
    # lookahead 오류
    "input c: series<float> = 0\nlet a = ta.security(c, 1)",
    # 자원 상한 초과
    "input c: series<float> = 0\n" + "plot(c)\n" * 33,
]


class FixedProvider:
    def __init__(self, source: str) -> None:
        self._source = source

    async def generate_script(self, *, prompt: str, hint: str | None = None) -> ScriptDraft:
        return ScriptDraft(source=self._source, provider_name="fixture", model_name="fixture-1")

    async def explain_script(self, *, source: str) -> str:
        raise NotImplementedError

    async def explain_backtest(self, *, summary: str) -> str:
        raise NotImplementedError


async def test_pass_rate_fixture_has_zero_silent_failures() -> None:
    statuses = []
    for source in FIXTURE_SOURCES:
        result = await generate_script(
            tenant_id=uuid4(),
            prompt="fixture",
            provider=FixedProvider(source),
            registry_version=REGISTRY_VERSION,
            usage_store=InMemoryCounter(),
            daily_cap=50,
            now=NOW,
        )
        statuses.append(result.status)

    report = build_pass_rate_report(statuses)
    assert report.attempted == len(FIXTURE_SOURCES)
    assert report.silent_failures == 0
    assert report.compiled == 1
    assert report.failed_with_explicit_reason == 4


# ---------------------------------------------------------------------------
# Negative tests — 불변식 위반 입력 거부 확인
# ---------------------------------------------------------------------------


async def test_empty_outcomes_list() -> None:
    """빈 리스트 입력 → attempted=0, silent_failures=0, pass_rate=1."""
    report = build_pass_rate_report([])
    assert report.attempted == 0
    assert report.silent_failures == 0
    assert report.pass_rate == 1  # empty → 100% (no failures possible)


async def test_invalid_status_string_ignored() -> None:
    """유효하지 않은 상태값("invalid", "COMPILED") → silent_failures 정확성 검증.

    build_pass_rate_report는 "compiled"와 "compile_failed"만 세고 나머지는
    silently count-out — attempted는 전체 개수이므로
    attempted - compiled - failed_with_explicit_reason > 0 이면
    silent_failures > 0 이 된다. 이 테스트는 유효하지 않은 상태값이
    silent_failures로 누산되는 것을 확인한다.
    """
    statuses = ["compiled", "invalid", "COMPILED", "compile_failed"]
    report = build_pass_rate_report(statuses)
    assert report.attempted == 4
    assert report.compiled == 1
    assert report.failed_with_explicit_reason == 1
    # "invalid"과 "COMPILED"는 어느 쪽에도 속하지 않으므로 silent_failures=2
    assert report.silent_failures == 2


async def test_all_outcomes_compiled() -> None:
    """모든 항목이 "compiled" → pass_rate=1, silent_failures=0."""
    report = build_pass_rate_report(["compiled"] * 5)
    assert report.attempted == 5
    assert report.compiled == 5
    assert report.failed_with_explicit_reason == 0
    assert report.silent_failures == 0
    assert report.pass_rate == 1


async def test_all_outcomes_failed() -> None:
    """모든 항목이 "compile_failed" → pass_rate=0, silent_failures=0."""
    report = build_pass_rate_report(["compile_failed"] * 3)
    assert report.attempted == 3
    assert report.compiled == 0
    assert report.failed_with_explicit_reason == 3
    assert report.silent_failures == 0
    assert report.pass_rate == 0


# ---------------------------------------------------------------------------
# Failure-injection test — 의존성 예외 유발
# ---------------------------------------------------------------------------


async def test_generate_script_provider_raises_exception() -> None:
    """provider.generate_script 가 예외 발생 → generate_script 가 예외를 전파.

    try-except 과다 포장 없이 실제 예외가 호출자에게 전파되는지 확인한다.
    """
    provider = FixedProvider(_VALID_FIXTURE_SOURCE)
    with patch.object(
        provider,
        "generate_script",
        new_callable=lambda: AsyncMock(side_effect=RuntimeError("provider crash")),
    ):
        try:
            await generate_script(
                tenant_id=uuid4(),
                prompt="injection test",
                provider=provider,
                registry_version=REGISTRY_VERSION,
                usage_store=InMemoryCounter(),
                daily_cap=50,
                now=NOW,
            )
        except RuntimeError as exc:
            assert str(exc) == "provider crash"
        else:
            raise AssertionError("Expected RuntimeError to propagate")


# ---------------------------------------------------------------------------
# Performance assertion — large outcome list
# ---------------------------------------------------------------------------


async def test_large_outcome_list_performance() -> None:
    """1000 개 항목 처리 → 시간 측정 및 성능 단언."""
    statuses = ["compiled", "compile_failed"] * 500
    start = time.perf_counter()
    report = build_pass_rate_report(statuses)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert report.attempted == 1000
    assert report.compiled == 500
    assert report.failed_with_explicit_reason == 500
    assert report.silent_failures == 0
    # 1000 개 항목 집계는 100ms 이내여야 함
    assert elapsed_ms < 100, f"build_pass_rate_report took {elapsed_ms:.1f}ms for 1000 items"
