"""DC-27 — `ports/source_contract_repository.py`의 asyncpg 구현.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D1. `source_contract` 테이블(마이그레이션 참조: 이 파일과 같은 리프에서
신설)을 읽기만 한다 — 등급 승격(행 UPDATE)은 운영 도구/관리 라우터
소관이고 이 어댑터는 아직 쓰기를 노출하지 않는다(계약 전 어댑터를
선등록하지 않는다는 D6과 같은 이유로, 쓰기 경로는 실제 계약 등록
운영절차가 정해지는 후속 리프에서 추가한다).
"""
from __future__ import annotations

import json

import asyncpg

from src.foundation.market_data.domain.entitlement.source_contract import (
    RedistributionScope,
    SourceCapability,
    SourceContract,
    SourceContractTier,
)

__all__ = ["PostgresSourceContractRepository"]


def _row_to_contract(row: asyncpg.Record) -> SourceContract:
    capability_raw = row["capability"]
    capability_dict = (
        json.loads(capability_raw) if isinstance(capability_raw, str) else capability_raw
    )
    return SourceContract(
        source_id=row["source_id"],
        tier=SourceContractTier(row["tier"]),
        credential_ref=row["credential_ref"],
        redistribution_scope=RedistributionScope(row["redistribution_scope"]),
        rate_limit=row["rate_limit"],
        quota=row["quota"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        capability=SourceCapability(
            asset_classes=frozenset(capability_dict["asset_classes"]),
            resolutions=frozenset(capability_dict["resolutions"]),
            corporate_actions=capability_dict["corporate_actions"],
        ),
    )


class PostgresSourceContractRepository:
    async def get(self, conn: asyncpg.Connection, source_id: str) -> SourceContract | None:
        row = await conn.fetchrow(
            "SELECT * FROM source_contract WHERE source_id = $1", source_id
        )
        if row is None:
            return None
        return _row_to_contract(row)
