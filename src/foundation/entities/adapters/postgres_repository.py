"""FA-2 — 엔티티 계층 4테이블의 asyncpg 저장소 구현.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-2
(§2.1 표·§3 계약), 105번(동시성 표준).

tenant 격리(decision: LA-22/PLT-27과 동일 패턴) — `LegalEntity`만
`tenant_id` 컬럼을 갖는다(FA-1 계약과 동일, Fund/Portfolio/SubAccount는
자기 tenant_id가 없다). 하위 3개 테이블 조회는 `legal_entity`까지
JOIN을 타고 `WHERE le.tenant_id = $1`을 반드시 건다 — `tenant_id`는
선택 파라미터가 아니라 모든 조회·폐쇄·`list_*_by_*` 메서드의 첫 필수
인자다. 존재하지 않는 id와 다른 tenant 소유 id는 항상 같은 `None`/빈
리스트로 접는다(404 동형, 호출부가 둘을 구분할 방법이 없다).

폐쇄(`closed_at`)는 조건부 UPDATE로만 쓴다 — `closed_at IS NULL`을 기대
상태로 걸어 동시 이중 폐쇄를 막는다(105번). 상위 폐쇄(legal_entity/fund/
portfolio)는 같은 UPDATE 문 안에 `NOT EXISTS(활성 자식)` 절을 추가로 건다
— 사전 SELECT(도메인 `validate_close_*`용 `list_*_by_*` 호출)와 이 UPDATE
사이에 다른 트랜잭션이 활성 자식을 INSERT하는 TOCTOU 경합을 막기 위함이다.
tenant 소유권을 EXISTS로 원자화한 기법을 그대로 확장한 것 — 도메인 판정
(`domain/hierarchy.py`)은 사전검사로만 남고 재구현하지 않는다, 이 DB 절이
최종 권위다. 조건부 UPDATE가 0행이면 "존재하지 않음/교차 테넌트"·"이미
폐쇄됨(경합)"·"활성 자식 존재(계층 위반)"를 구분해야 하므로
`activate_revision()`(mandates 어댑터)과 같은 방식으로 재조회해 갈라
던진다.

이 파일은 300줄 상한(P6) 때문에 엔티티 레벨별 믹스인(`_legal_entity_
repository.py`/`_fund_repository.py`/`_portfolio_repository.py`/
`_sub_account_repository.py`)으로 나눈 실제 구현을 그대로 합성만 한다 —
쿼리 로직은 그 파일들에 있다.
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
