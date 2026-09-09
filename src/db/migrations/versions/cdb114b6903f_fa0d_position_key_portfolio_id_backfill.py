"""FA-0d -- position_key portfolio_id 편입: pos_snapshot 백필.

Revision ID: cdb114b6903f
Revises: 18965d657219
Create Date: 2026-09-09 05:00:00.000000

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-0d
(§9 표 113행). task-1943.

`domain/position_key.py`(`PositionKey`)가 4부분
(`venue:instrument_id:strategy_id:execution_id`)에서 5부분(+`portfolio_id`)으로
바뀌었다(task-1943) -- 다포트폴리오 전환 이후 같은 venue/instrument/strategy/
execution 조합이 서로 다른 포트폴리오에서 동시에 열릴 수 있어 4부분만으로는
더 이상 포지션을 유일하게 식별하지 못하기 때문이다. `record_fill`/
`rebuild_snapshot`/`record_funding_fee`가 이제 `PositionKey.parse()`로 5부분
형식을 강제하므로, 기존 `pos_snapshot` 행의 `position_key`(PK)도 새 형식으로
다시 써야 그 행들이 계속 읽힌다.

`portfolio_id` 값은 새로 계산하지 않는다 -- FA-4(`963d5f3cfb1b`)가 이미
`pos_snapshot.portfolio_id` 컬럼을 tenant_id별 FA-1 기본 포트폴리오로 백필해
뒀으므로 그 컬럼값을 그대로 재사용한다(관리자가 그 이후 실제로 다른
포트폴리오로 재배정했더라도 이 컬럼이 SSOT다).

역산 불가 처리(PM decision, task-1943): FA-4는 백필 실패 행(포트폴리오
미부트스트랩 tenant)을 조용히 건너뛰었지만, 이 리프는 `position_key` 자체
(PK)를 바꾸는 것이라 조용히 건너뛰면 그 행은 새 5부분 형식을 강제하는
`PositionKey.parse()` 검사를 영원히 통과하지 못하는 접근 불가능한 좀비 행이
된다. 그래서 `portfolio_id`가 NULL이거나 기존 `position_key`가 정확히
4부분이 아닌 행이 하나라도 있으면 마이그레이션을 그 자리에서 실패시킨다
(운영자가 먼저 FA-4 백필/포트폴리오 부트스트랩을 끝내야 한다). 이미 5부분
형식(끝 구성요소가 유효한 UUID)인 행은 그대로 건너뛴다(재실행 멱등).

WORM: `pos_journal`은 손대지 않는다(`963d5f3cfb1b`과 같은 이유 -- append-only
트리거가 물리적으로 UPDATE/DELETE를 막는다, I-04 위반 우회 금지). 그 결과
이 마이그레이션 시점에 이미 저널 엔트리가 있던 포지션은 백필 이후
새 `position_key`로 `journal.list_for()`를 조회하면 그 이전 엔트리가 보이지
않는다(옛 키로만 조회 가능) -- FA-4가 `pos_journal.fund_id`/`portfolio_id`를
영구 NULL로 남긴 것과 같은 부류의 WORM 기술부채다.

실DB(TEST_DATABASE_URL) 확인 결과(task-1943 note): 착수 시점 로컬 테스트 DB는
`pos_snapshot`/`pos_journal` 둘 다 0행이라 이 백필 경로 자체는 실행되지
않았다 -- `pos_journal.UNIQUE(position_key, sequence_no)`는 이 마이그레이션이
`pos_journal`을 전혀 건드리지 않으므로 구조적으로 영향을 받지 않는다(신규
쓰기만 새 5부분 키를 쓰고, 기존 행은 옛 키를 그대로 유지 -- 두 형식이 같은
문자열 충돌을 일으킬 실질적 경우는 없다). `tests/foundation/integration/
positions/test_migration_fa0d_position_key_portfolio_id.py`가 합성 행으로
백필 성공/실패(NULL portfolio_id) 두 분기를 실DB에 대해 검증한다.

`pos_snapshot`에는 `no_update_guard`(`a2c4f9e1b3d5`, FA-10)가 걸려 있어 PK인
`position_key`를 UPDATE로 바꿀 수 없다 -- `position_ledger.py`가
`legacy_position_id` 갱신에 쓰는 것과 같은 DELETE(old) + INSERT(new, 나머지
컬럼 동일) 패턴으로 대체한다.
"""
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "cdb114b6903f"
down_revision: str | Sequence[str] | None = "18965d657219"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    "position_key", "tenant_id", "account_id", "instrument_id", "quantity", "avg_cost",
    "cost_method", "lots", "realized_pnl_base", "unrealized_pnl_base", "fees_base",
    "funding_base", "mark_price", "mark_at", "last_journal_seq", "legacy_position_id",
    "fund_id", "portfolio_id", "valid_from", "valid_to", "tx_from", "tx_to", "updated_at",
)
_OTHER_COLUMNS = ", ".join(c for c in _COLUMNS if c != "position_key")


class UnbackfillablePositionKeyError(RuntimeError):
    """역산 불가 `pos_snapshot` 행을 만나 마이그레이션을 그 자리에서 멈춘다."""


_REPLACE_SQL = (
    "WITH prior AS ("  # noqa: S608 -- 컬럼명은 상수 튜플(_COLUMNS)에서만 오고, 값은 전부 바인드 파라미터
    "  DELETE FROM pos_snapshot WHERE position_key = :old_key RETURNING *"
    ") "
    f"INSERT INTO pos_snapshot (position_key, {_OTHER_COLUMNS}) "
    f"SELECT :new_key, {_OTHER_COLUMNS} FROM prior"
)


def _replace_position_key(old_key: str, new_key: str) -> None:
    op.get_bind().execute(text(_REPLACE_SQL), {"old_key": old_key, "new_key": new_key})


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(text("SELECT position_key, portfolio_id FROM pos_snapshot")).fetchall()

    for position_key, portfolio_id in rows:
        parts = position_key.split(":")
        if len(parts) == 5:
            continue  # 이미 새 형식(재실행 멱등)
        if len(parts) != 4:
            raise UnbackfillablePositionKeyError(
                f"pos_snapshot.position_key={position_key!r}: 4부분 레거시 형식이 "
                "아니라 자동 백필할 수 없습니다(역산 불가)."
            )
        if portfolio_id is None:
            raise UnbackfillablePositionKeyError(
                f"pos_snapshot.position_key={position_key!r}: portfolio_id가 NULL입니다"
                "(FA-4 백필 미완료) -- 먼저 해당 tenant의 기본 포트폴리오를 "
                "부트스트랩하세요(역산 불가)."
            )
        new_key = ":".join((*parts, str(portfolio_id)))
        _replace_position_key(position_key, new_key)


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(text("SELECT position_key FROM pos_snapshot")).fetchall()

    for (position_key,) in rows:
        parts = position_key.split(":")
        if len(parts) != 5:
            continue  # 이미 옛 형식
        old_key = ":".join(parts[:4])
        _replace_position_key(position_key, old_key)
