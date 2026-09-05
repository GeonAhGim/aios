"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-12 —
`artifact/hash.py` 테스트.

DoD(task-1535 note): 같은 입력=같은 해시, 레지스트리 버전 변경=다른 해시.
그 외 네 입력(소스·IR·레지스트리·문법 버전) 각각이 해시에 실제로 기여함과
negative(빈 소스·빈 레지스트리 버전·IRProgram 아님)를 단언한다. 입력은
DSL-3 `parse()`→DSL-7 `lower_program()`의 실제 산출물이다.
"""
from __future__ import annotations

import re

import pytest

from src.core.script.artifact import HASH_SCHEMA, hash_payload, script_hash
from src.core.script.grammar.ast import GRAMMAR_VERSION
from src.core.script.grammar.parser import parse
from src.core.script.ir import IRProgram, lower_program

SAMPLE = (
    "input length: int = 14\n"
    "input close: series<float> = 0\n"
    "let rsi_val = ta.rsi(close, length)\n"
    "signal go_long = rsi_val < 30\n"
)
REGISTRY_V1 = "a" * 64
REGISTRY_V2 = "b" * 64
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _ir(source: str) -> IRProgram:
    return lower_program(parse(source))


def _hash(source: str = SAMPLE, registry_version: str = REGISTRY_V1, **kw: str) -> str:
    return script_hash(source=source, ir=_ir(source), registry_version=registry_version, **kw)


# ---- 결정론 ----


def test_same_input_yields_same_hash() -> None:
    first, second = _hash(), _hash()
    assert first == second
    assert _HEX64.match(first)


def test_hash_is_stable_across_fresh_parse_and_lower() -> None:
    a = script_hash(source=SAMPLE, ir=_ir(SAMPLE), registry_version=REGISTRY_V1)
    b = script_hash(source=SAMPLE, ir=_ir(SAMPLE), registry_version=REGISTRY_V1)
    assert a == b


# ---- 네 입력 각각이 기여 ----


def test_registry_version_change_changes_hash() -> None:
    assert _hash(registry_version=REGISTRY_V1) != _hash(registry_version=REGISTRY_V2)


def test_grammar_version_change_changes_hash() -> None:
    assert _hash() != _hash(grammar_version="aios-script-999")


def test_source_whitespace_change_changes_hash_even_with_same_ir() -> None:
    reformatted = SAMPLE.replace("ta.rsi(close, length)", "ta.rsi( close,length )")
    assert _ir(SAMPLE) == _ir(reformatted)  # IR은 동일
    assert _hash(SAMPLE) != _hash(reformatted)  # 소스는 신원의 일부


def test_ir_change_changes_hash() -> None:
    other = SAMPLE.replace("< 30", "< 40")
    assert _hash(SAMPLE) != _hash(other)


def test_payload_exposes_all_four_inputs() -> None:
    payload = hash_payload(source=SAMPLE, ir=_ir(SAMPLE), registry_version=REGISTRY_V1)
    assert set(payload) == {
        "schema",
        "grammar_version",
        "ir_version",
        "ir",
        "registry_version",
        "source",
    }
    assert payload["schema"] == HASH_SCHEMA
    assert payload["grammar_version"] == GRAMMAR_VERSION
    assert payload["registry_version"] == REGISTRY_V1


# ---- negative ----


@pytest.mark.parametrize("source", ["", "   \n"])
def test_empty_source_rejected(source: str) -> None:
    with pytest.raises(ValueError, match="소스"):
        script_hash(source=source, ir=_ir(SAMPLE), registry_version=REGISTRY_V1)


def test_empty_registry_version_rejected() -> None:
    with pytest.raises(ValueError, match="레지스트리"):
        script_hash(source=SAMPLE, ir=_ir(SAMPLE), registry_version="")


def test_empty_grammar_version_rejected() -> None:
    with pytest.raises(ValueError, match="문법"):
        script_hash(source=SAMPLE, ir=_ir(SAMPLE), registry_version=REGISTRY_V1, grammar_version="")


def test_non_ir_program_rejected() -> None:
    with pytest.raises(ValueError, match="IRProgram"):
        script_hash(source=SAMPLE, ir=parse(SAMPLE), registry_version=REGISTRY_V1)  # type: ignore[arg-type]
