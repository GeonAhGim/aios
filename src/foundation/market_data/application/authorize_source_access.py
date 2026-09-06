"""DC-27 — source_id만 아는 어댑터 앞단 게이트.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D1. 향후 유료 ingest_source 어댑터가 호출 전에 거치는 유일한 지점이다 —
어댑터 함수 시그니처는 `source_id` 문자열만 받고, 이 게이트가
`source_contract` 행을 읽어 등급·능력·재배포 스코프를 판정한다. 등급
승격은 그 행의 UPDATE뿐이므로 이 함수도 어댑터 코드도 바뀌지 않는다
(task-1764 DoD).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import asyncpg

from src.foundation.market_data.domain.entitlement.source_contract import (
    SourceContractGrant,
    authorize_source,
)
from src.foundation.market_data.ports.source_contract_repository import SourceContractRepository

__all__ = ["authorize_source_access"]


async def authorize_source_access(
    conn: asyncpg.Connection,
    source_id: str,
    *,
    repo: SourceContractRepository,
    clock: Callable[[], datetime],
) -> SourceContractGrant:
    """어댑터가 `source_id` 데이터를 실제로 가져오기 전에 호출한다. 반환된
    `SourceContractGrant.allowed`가 `False`면 어댑터는 호출되지 않아야
    한다 — 판정은 여기서 끝나고, 어댑터는 등급을 아예 전달받지 않는다."""
    contract = await repo.get(conn, source_id)
    return authorize_source(contract, clock())
