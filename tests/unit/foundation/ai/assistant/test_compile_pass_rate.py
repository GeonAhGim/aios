"""U-3a -- 컴파일 통과율 회귀 픽스처.

Spec DoD: "생성된 스크립트 100%가 DSL 컴파일·리소스 상한 검사를 통과하거나
실패 사유를 사용자에게 명시(무음 실패 0건, 회귀 CI 픽스처)". 아래 픽스처는
provider가 낼 법한 대표 산출물(정상/구문오류/미래참조/자원상한초과) 각각을
`generate_script`에 통과시켜 매번 명시적 성공 또는 명시적 실패만 나오는지
집계한다.
"""

from __future__ import annotations

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
