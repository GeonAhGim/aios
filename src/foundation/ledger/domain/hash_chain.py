"""LC-3 — 원장 분개 해시 체인.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.3, §4.3, §9 LC-3.

FND-03(`src/foundation/evidence/domain/rules.py`)과 같은 방식: 이전 엔트리의
`entry_hash`를 다음 엔트리의 `prev_hash`로 엮어 변조 시 체인이 끊어지게
한다. 이 모듈은 순수 함수만 담는다 — I/O·시계 직접 호출 금지, 시각은 항상
인자로 받는다.

`lines_digest`는 행 **순서 무관**이다: 같은 분개 행 집합을 다른 순서로
넘겨도 동일한 다이제스트가 나온다(호출자가 DB에서 다른 정렬로 읽어와도
검증이 흔들리지 않도록).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from src.foundation.ledger.contracts.v1 import JournalEntryView, LedgerEventType, PostingLine


def canonical_json(data: Any) -> str:
    """정렬된 키로 결정적 JSON 문자열을 만든다(`idempotency.py`가 재사용)."""
    return json.dumps(data, sort_keys=True, default=str)


def lines_digest(lines: Sequence[PostingLine]) -> str:
    """분개 행들의 다이제스트. 입력 순서와 무관하게 동일한 값을 낸다."""
    canonical = [
        {
            "line_no": line.line_no,
            "account_code": line.account_code,
            "side": line.side.value,
            "amount": str(line.amount),
            "currency": line.currency.value,
        }
        for line in lines
    ]
    canonical.sort(
        key=lambda row: (row["account_code"], row["side"], row["amount"], row["line_no"])
    )
    return hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()


def entry_hash(
    prev: str | None,
    seq: int,
    event_type: LedgerEventType,
    event_ref: str,
    digest: str,
    posted_at: datetime,
) -> str:
    """체인의 링크 하나. `prev`가 None이면(전역 첫 분개) 빈 문자열로 취급해
    체인이 항상 결정론적으로 시작하게 한다."""
    payload = "|".join(
        [prev or "", str(seq), event_type.value, event_ref, digest, posted_at.isoformat()]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ChainIntegrityError(Exception):
    """`INTEGRITY_LEDGER_CHAIN_BROKEN` — 변조되거나 빠진 구간을 발견했다.
    `detail`에 어느 sequence_no에서 깨졌는지 남긴다."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def verify_chain(entries: Sequence[JournalEntryView]) -> None:
    """`sequence_no` 오름차순으로 정렬된 분개 목록이 진짜 체인인지 확인한다.
    문제가 없으면 조용히 반환하고, 있으면 `ChainIntegrityError`를 던진다."""
    expected_prev: str | None = None
    for entry in entries:
        if entry.prev_hash != expected_prev:
            raise ChainIntegrityError(
                f"sequence_no={entry.sequence_no}: prev_hash가 이전 엔트리의 "
                "entry_hash와 일치하지 않습니다(체인 단절 또는 변조)."
            )
        recomputed_digest = lines_digest(entry.lines)
        if recomputed_digest != entry.lines_digest:
            raise ChainIntegrityError(
                f"sequence_no={entry.sequence_no}: lines_digest가 행들로부터 "
                "재계산한 값과 다릅니다(행 내용 변조 의심)."
            )
        recomputed_hash = entry_hash(
            entry.prev_hash,
            entry.sequence_no,
            entry.event_type,
            entry.event_ref,
            recomputed_digest,
            entry.posted_at,
        )
        if recomputed_hash != entry.entry_hash:
            raise ChainIntegrityError(
                f"sequence_no={entry.sequence_no}: entry_hash가 필드로부터 "
                "재계산한 값과 다릅니다(내용 변조 의심)."
            )
        expected_prev = entry.entry_hash
