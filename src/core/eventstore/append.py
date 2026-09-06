"""FA-13 — 이벤트 스토어 조건부 append(해시 체인).

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§2.4 FA-13, §5.

§5 표 그대로 "이벤트 append: `(stream_id, seq)` 유일 + 조건부 삽입"이 이
모듈의 동시성 전략이다. LB-9(`positions/adapters/postgres_journal_repository.py`)
·evidence `postgres_repository.py`의 advisory lock 방식과 달리, 이벤트
스토어는 스트림이 매우 많고(주문마다·포지션마다·분개마다 하나) 전역 락
네임스페이스를 공유시키는 대신 `(stream_id, seq)` UNIQUE 제약 + `WHERE`
조건부 INSERT만으로 fail-closed 직렬화를 얻는다 — 경쟁에서 진 쪽은
`SequenceConflictError`로 즉시 재시도를 요구받는다(락 대기 없음).

해시 체인의 정규화·다이제스트 규칙은 원장 LC-3
`src/foundation/ledger/domain/hash_chain.py`의 `canonical_json`을 그대로
재사용한다(task-1703 decision — 재구현 금지). `event_hash`가 필드를 이어붙여
sha256하는 모양은 그 파일의 `entry_hash`와 같은 레시피를 이벤트 필드
집합(대상 필드 자체는 다름)에 맞춰 다시 쓴 것이다 — 정규화 함수 자체를
복제하지 않았다.

이 리프는 마이그레이션을 만들지 않는다(task-1703 decision: "테이블 DDL이
필요하면 같은 커밋에 넣지 말고 needs_decision으로 물어라"). `event_store`
영구 스키마는 PM이 parent를 정하는 별도 리프(FA-14 이전)의 몫이다 — 이
파일은 테이블 이름(`TABLE`)과 조건부 SQL만 정의하고, 실 DB 통합테스트는
자신의 트랜잭션 안에서 임시 테이블을 만들어 검증한다
(`tests/integration/core/eventstore/conftest.py`).

`conn`은 호출자가 이미 연 `asyncpg.Connection`을 그대로 받는다 — 이 모듈은
자체 `pool.acquire`를 하지 않는다(LC-4/LB-11 선례). 커밋은 호출자 책임.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

import asyncpg

from src.core.eventstore.contracts.v1 import DomainEvent
from src.foundation.ledger.domain.hash_chain import canonical_json

TABLE = "event_store"


class SequenceConflictError(Exception):
    """`ES_SEQUENCE_CONFLICT` — 다른 append가 먼저 이 `seq`를 차지했다
    (재시도 가능). 호출자는 스트림의 최신 seq를 다시 조회한 뒤 재시도한다 —
    이 함수는 재전송을 스스로 흡수하지 않는다(멱등키 REPLAY가 아니라
    낙관적 동시성 제어)."""

    def __init__(self, stream_id: str, expected_seq: int) -> None:
        super().__init__(
            f"stream_id={stream_id!r}: seq={expected_seq}는 더 이상 다음 seq가 "
            "아닙니다(동시 append 충돌) — 최신 seq를 다시 조회한 뒤 재시도하세요."
        )
        self.stream_id = stream_id
        self.expected_seq = expected_seq


def payload_digest(payload: dict[str, Any]) -> str:
    """payload의 결정적 다이제스트. `canonical_json`(LC-3 재사용)이 키를
    정렬하므로 같은 payload는 파이썬 dict 순서와 무관하게 같은 값이 된다."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def event_hash(
    prev_hash: str | None,
    stream_id: str,
    seq: int,
    event_type: str,
    digest: str,
    occurred_at: datetime,
) -> str:
    """체인의 링크 하나. `prev_hash`가 없으면(스트림의 첫 이벤트) 빈
    문자열로 취급해 체인이 항상 결정론적으로 시작하게 한다(LC-3
    `entry_hash`와 같은 레시피)."""
    payload = "|".join(
        [prev_hash or "", stream_id, str(seq), event_type, digest, occurred_at.isoformat()]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _row_to_event(row: asyncpg.Record) -> DomainEvent:
    return DomainEvent(
        stream_id=row["stream_id"],
        seq=row["seq"],
        type=row["type"],
        payload=json.loads(row["payload"]),
        occurred_at=row["occurred_at"],
        recorded_at=row["recorded_at"],
        causation_id=row["causation_id"],
        correlation_id=row["correlation_id"],
        hash=row["hash"],
        prev_hash=row["prev_hash"],
    )


async def append(
    conn: asyncpg.Connection,
    *,
    stream_id: str,
    expected_seq: int,
    type: str,
    payload: dict[str, Any],
    occurred_at: datetime,
    recorded_at: datetime,
    causation_id: str | None = None,
    correlation_id: str | None = None,
) -> DomainEvent:
    """`expected_seq`로 조건부 append한다.

    `expected_seq`는 이 이벤트가 가져야 할 seq 값(스트림의 현재 head + 1)이다.
    스트림의 실제 head가 그와 다르면(동시 append가 먼저 채웠거나, 호출자가
    오래된 head를 보고 있었거나) `SequenceConflictError`를 던진다 — insert를
    시도하기 전에 조회한 head로 한 번 걸러내고(왕복 절약), 조회와 INSERT
    사이에 경쟁이 끼어든 경우까지 대비해 `WHERE` 조건부 INSERT의 0-row
    RETURNING과 `(stream_id, seq)` UNIQUE 위반 둘 다를 최종 게이트로 삼는다
    (락 없이 fail-closed).
    """
    if occurred_at.tzinfo is None or recorded_at.tzinfo is None:
        raise ValueError("occurred_at/recorded_at는 tz-aware(UTC)여야 합니다")

    head = await conn.fetchrow(
        f"SELECT seq, hash FROM {TABLE} WHERE stream_id = $1 ORDER BY seq DESC LIMIT 1",  # noqa: S608
        stream_id,
    )
    last_seq: int = 0 if head is None else head["seq"]
    prev_hash: str | None = None if head is None else head["hash"]
    if expected_seq != last_seq + 1:
        raise SequenceConflictError(stream_id, expected_seq)

    digest = payload_digest(payload)
    new_hash = event_hash(prev_hash, stream_id, expected_seq, type, digest, occurred_at)

    try:
        row = await conn.fetchrow(
            f"INSERT INTO {TABLE} "  # noqa: S608 -- TABLE은 모듈 상수, 사용자 입력 아님
            "(stream_id, seq, type, payload, occurred_at, recorded_at, "
            " causation_id, correlation_id, hash, prev_hash) "
            "SELECT $1::varchar, $2::int, $3::varchar, $4::jsonb, $5::timestamptz, "
            "$6::timestamptz, $7::varchar, $8::varchar, $9::varchar, $10::varchar "
            f"WHERE COALESCE((SELECT MAX(seq) FROM {TABLE} WHERE stream_id = $1), 0) = $2 - 1 "
            "RETURNING *",
            stream_id,
            expected_seq,
            type,
            json.dumps(payload),
            occurred_at,
            recorded_at,
            causation_id,
            correlation_id,
            new_hash,
            prev_hash,
        )
    except asyncpg.UniqueViolationError as exc:
        raise SequenceConflictError(stream_id, expected_seq) from exc

    if row is None:
        raise SequenceConflictError(stream_id, expected_seq)

    return _row_to_event(row)
