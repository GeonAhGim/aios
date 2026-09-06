"""FA-13 — 이벤트 스토어 계약 v1.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§2.4 FA-13,
107_contract_versioning_and_compatibility_standard_v1.0.md.

`DomainEvent`는 `core/eventstore/`의 유일한 공개 표면이다 — `append.py`·
`replay.py`(FA-15)·`projections/*.py`(FA-14)는 전부 이 계약을 통해서만
이벤트를 주고받는다. 필드 추가는 minor(107번, 기본값 필수) — 제거·의미
변경은 `v2` 모듈 신설.

`hash`/`prev_hash`는 호출자가 계산해 넘기는 값이 아니라 어댑터
(`append.py`)가 `(stream_id, seq)` UNIQUE 제약 + 조건부 INSERT로 부여하는
해시체인 링크다(§5).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel

SCHEMA_VERSION: Literal["v1"] = "v1"


class DomainEvent(BaseModel):
    """append-only `event_store` 행 하나의 뷰(§2.4 표)."""

    stream_id: str
    seq: int
    type: str
    payload: dict[str, Any]
    occurred_at: AwareDatetime
    recorded_at: AwareDatetime
    causation_id: str | None
    correlation_id: str | None
    hash: str
    prev_hash: str | None
    schema_version: Literal["v1"] = SCHEMA_VERSION
