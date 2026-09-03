"""LA-8/LA-23b — 배치 계보(lineage) 해시. §A4(문서 §31행) 감사 이벤트 바인딩용 다이제스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-8, §9.2 LA-8,
docs/design/ADR-2026-09-04-A-market-data-replay-perf.md#2.

`src/foundation/ledger/domain/hash_chain.py`의 canonical JSON 규칙(정렬된
키·UTF-8·`default=str`로 Decimal 등을 문자열화)을 그대로 따른다. `batch_hash`는
`hash_chain.lines_digest`와 같은 이유로 입력 순서 무관이어야 한다 — 저장소가
다른 정렬로 레코드를 읽어 오더라도 같은 배치는 같은 해시를 내야 검증이
흔들리지 않는다. I/O 없음 — 순수 함수만 담는다.

**ADR-2026-09-04-A #2 스트리밍 재구현 노트**: ADR 본문은 "ORDER BY로 가져오면
전체 정렬이 사라진다"고 적었지만, 이 함수는 `ingest_candles`/`ingest_ticks`가
쓰기 시점에 이미 저장한 `batch_hash` 값과 바이트 단위로 동일해야 한다(P3
WORM, 저장 해시 재계산·backfill 금지 — 같은 ADR #2). 정렬 키는 레코드의
canonical JSON 문자열 자체(주로 첫 알파벳 필드 값, 예: `CandleRecord`는
`close`)라 DB의 `open_time ORDER BY`와 무관하다 — 그래서 이 구현은 정렬은
그대로 두고, 대신 두 가지만 없앤다: (1) 대량 소비자는 이제 컬럼지향 경로
(`domain/candle_columns`)로 읽어 레코드 생성 자체의 pydantic 검증 비용이
빠졌고, (2) 정렬된 문자열을 `"\\n".join()`으로 한 번에 이어붙인 뒤 해시하던
방식을, 큰 중간 문자열을 만들지 않는 증분 `hashlib` 스트리밍으로 바꿨다.
sha256(Merkle–Damgård 계열)은 연속된 `update()` 호출과 이어붙인 전체를 한
번에 해시한 것이 항상 바이트 단위로 같다 — `_batch_hash_reference`(옛 구현,
비교 전용으로 보존)와의 동일성은
`tests/unit/market_data/test_lineage.py`의 property 테스트(무작위 배치
200건 이상)가 증명한다.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

__all__ = ["batch_hash", "request_fingerprint"]


def _canonical_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, default=str)


def _batch_hash_reference(records: Sequence[Any]) -> str:
    """옛 구현 — 삭제 금지. `batch_hash`가 계속 이 함수와 바이트 단위로
    동일한 값을 내는지 property 테스트가 대조하는 기준선이다(모듈 docstring)."""
    canonical_rows = sorted(_canonical_json(record) for record in records)
    payload = "\n".join(canonical_rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def batch_hash(records: Sequence[Any]) -> str:
    """레코드 배치의 다이제스트. 입력 순서와 무관하게 동일한 값을 내며,
    `_batch_hash_reference`와 바이트 단위로 동일하다(모듈 docstring 참고).

    각 레코드를 정렬된 키의 canonical JSON 문자열로 만든 뒤, 그 문자열들을
    사전순으로 정렬하고, 하나의 문자열로 합치지 않고 구분자 `\\n`과 함께
    순서대로 `hashlib`에 흘려 넣는다(증분 스트리밍)."""
    canonical_rows = sorted(_canonical_json(record) for record in records)
    hasher = hashlib.sha256()
    for index, row in enumerate(canonical_rows):
        if index:
            hasher.update(b"\n")
        hasher.update(row.encode("utf-8"))
    return hasher.hexdigest()


def request_fingerprint(source: str, params: Mapping[str, Any]) -> str:
    """요청 지문: source + 정렬된 파라미터의 canonical JSON sha256."""
    payload = _canonical_json({"source": source, "params": dict(params)})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
