"""L4_analytics_authoring_backtest_marketplace_v1.0.md §2.4 표 90행/§9.4 DSL-12 —
`script_hash`: 소스·IR·지표 레지스트리 버전·문법 버전 → 콘텐츠 해시.

순수 함수(I/O 없음). 네 입력을 정준 JSON(`sort_keys`, 고정 구분자,
`ensure_ascii`)으로 직렬화해 sha256 hex(64자)를 낸다. IR은 DSL-7
`to_bytes()`(결정론 직렬화)를 그대로 쓰므로 "같은 AST=같은 IR 바이트"
계약이 그대로 "같은 IR=같은 script_hash"로 이어진다.

왜 네 입력인가(§3.3·§3.4): `ta.*`는 IND 레지스트리 버전에 고정되므로
레지스트리가 바뀌면 같은 소스라도 다른 산출물이다 — 그래서 IND-1
`IndicatorRegistry.registry_hash()`(스펙 정준 해시)를 `registry_version`
으로 받는다. 문법 버전은 IR 안에도 있지만(`IRProgram.grammar_version`)
페이로드 최상위에 명시해 IR 직렬화 형식이 바뀌어도 해시 입력 계약이
읽히게 한다. 소스는 정규화하지 않는다(공백만 달라도 다른 스크립트
아티팩트 — 마켓플레이스(MP-1)가 소스 그대로를 게시하므로 소스 바이트가
곧 신원이다). 재현 키(BT-9)는 이 값을 첫 항으로 쓴다.

Fail-closed: 빈 소스·빈 레지스트리 버전·IRProgram이 아닌 값은 `ValueError`
로 거부한다 — 잘못된 입력으로 그럴듯한 해시를 만들지 않는다.
"""
from __future__ import annotations

import hashlib
import json
from typing import Final

from src.core.script.grammar.ast import GRAMMAR_VERSION
from src.core.script.ir.ops import IRProgram, to_bytes

HASH_ALGORITHM: Final = "sha256"
HASH_SCHEMA: Final = "script-hash-1"


def hash_payload(
    *,
    source: str,
    ir: IRProgram,
    registry_version: str,
    grammar_version: str = GRAMMAR_VERSION,
) -> dict[str, str]:
    """해시 입력 4종을 검증해 정준 페이로드(dict)로 만든다. 테스트·감사용 공개."""
    if not isinstance(source, str) or not source.strip():
        raise ValueError("script_hash: 소스가 비어 있습니다")
    if not isinstance(registry_version, str) or not registry_version:
        raise ValueError("script_hash: 레지스트리 버전이 비어 있습니다")
    if not isinstance(grammar_version, str) or not grammar_version:
        raise ValueError("script_hash: 문법 버전이 비어 있습니다")
    if not isinstance(ir, IRProgram):
        raise ValueError(f"script_hash: IRProgram이 아닙니다: {type(ir).__name__}")
    return {
        "schema": HASH_SCHEMA,
        "grammar_version": grammar_version,
        "ir_version": ir.ir_version,
        "ir": to_bytes(ir).decode("utf-8"),
        "registry_version": registry_version,
        "source": source,
    }


def script_hash(
    *,
    source: str,
    ir: IRProgram,
    registry_version: str,
    grammar_version: str = GRAMMAR_VERSION,
) -> str:
    """소스·IR·레지스트리 버전·문법 버전 → sha256 hex(64자). 같은 입력 = 같은 값."""
    payload = hash_payload(
        source=source,
        ir=ir,
        registry_version=registry_version,
        grammar_version=grammar_version,
    )
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = ["HASH_ALGORITHM", "HASH_SCHEMA", "hash_payload", "script_hash"]
