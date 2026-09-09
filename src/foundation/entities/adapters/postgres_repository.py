"""FA-2 — 엔티티 계층 4테이블의 asyncpg 저장소 구현.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-2
(§2.1 표·§3 계약), 105번(동시성 표준).

tenant 격리(decision: LA-22/PLT-27과 동일 패턴) — `LegalEntity`만
`tenant_id` 컬럼을 갖는다(FA-1 계약과 동일, Fund/Portfolio/SubAccount는
자기 tenant_id가 없다). 하위 3개 테이블 조회는 `legal_entity`까지
JOIN을 타고 `WHERE le.tenant_id = $1`을 반드시 건다 — `tenant_id`는
it is not an optional parameter, it is the first required argument of every
lookup/close/`list_*_by_*` method. A nonexistent id and an id owned by a
different tenant always fold into the same `None`/empty list (404 isomorphism
-- the caller has no way to tell them apart).

Close (`closed_at`) is only ever written by a conditional UPDATE --
`closed_at IS NULL` is asserted as the expected state to block a concurrent
double-close (standard 105). Closing a parent (legal_entity/fund/portfolio)
adds a `NOT EXISTS(active child)` clause inside that same UPDATE statement
-- this closes the TOCTOU race where another transaction inserts an active
child between the pre-check SELECT (the `list_*_by_*` call domain's
`validate_close_*` makes) and this UPDATE. It is a straightforward extension
of the same technique used to atomize tenant ownership with EXISTS -- the
domain check (`domain/hierarchy.py`) stays a pre-check only and is not
reimplemented here; this DB clause is the final authority. When the
conditional UPDATE matches 0 rows, "does not exist/cross-tenant",
"already closed (race)", and "active child exists (hierarchy violation)"
must be told apart, so it re-queries and dispatches the same way
`activate_revision()` (the mandates adapter) does.

This file only composes the real implementations, which were split into
per-entity-level mixins (`_legal_entity_repository.py`,
`_fund_repository.py`, `_portfolio_repository.py`,
`_sub_account_repository.py`) because of the 300-line cap (P6) -- the query
logic lives in those files.
"""
from __future__ import annotations

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.entities.adapters._fund_repository import FundRepositoryMixin
from src.foundation.entities.adapters._legal_entity_repository import (
    LegalEntityRepositoryMixin,
)
from src.foundation.entities.adapters._portfolio_repository import PortfolioRepositoryMixin
from src.foundation.entities.adapters._sub_account_repository import SubAccountRepositoryMixin


class PostgresEntityRepository(
    LegalEntityRepositoryMixin,
    FundRepositoryMixin,
    PortfolioRepositoryMixin,
    SubAccountRepositoryMixin,
):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool


__all__ = ["PostgresEntityRepository", "ConcurrencyConflictError"]
