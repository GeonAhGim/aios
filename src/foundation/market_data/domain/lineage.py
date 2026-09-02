"""LA-8 — 배치 계보(lineage) 해시. §A4(문서 §31행) 감사 이벤트 바인딩용 다이제스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-8, §9.2 LA-8.

`src/foundation/ledger/domain/hash_chain.py`의 canonical JSON 규칙(정렬된
키·UTF-8·`default=str`로 Decimal 등을 문자열화)을 그대로 따른다. `batch_hash`는
`hash_chain.lines_digest`와 같은 이유로 입력 순서 무관이어야 한다 — 저장소가
다른 정렬로 레코드를 읽어 오더라도 같은 배치는 같은 해시를 내야 검증이
흔들리지 않는다. I/O 없음 — 순수 함수만 담는다.
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


def batch_hash(records: Sequence[Any]) -> str:
    """레코드 배치의 다이제스트. 입력 순서와 무관하게 동일한 값을 낸다.

    각 레코드를 정렬된 키의 canonical JSON 문자열로 만든 뒤, 그 문자열들을
    사전순으로 정렬해 이어붙여 해시한다."""
    canonical_rows = sorted(_canonical_json(record) for record in records)
    payload = "\n".join(canonical_rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def request_fingerprint(source: str, params: Mapping[str, Any]) -> str:
    """요청 지문: source + 정렬된 파라미터의 canonical JSON sha256."""
    payload = _canonical_json({"source": source, "params": dict(params)})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
