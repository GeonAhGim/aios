"""connections_live_provider — FND-05 실 provider 실체화(감사 §6)

Revision ID: 5ed4921f9873
Revises: b3f7e0c1a4d5
Create Date: 2026-09-03 01:00:00.000000

Spec: AIOSproject 74_connected_asset_l3_build_and_operational_specification_v1.0.md §1/§3.

전수감사(agent-platform-12, docs/FULL_AUDIT_2026-09-02.md §6) 발견 — 운영 DI가
`FakeReadonlyAccountProvider`를 반환하고 `account_snapshot`에 잔고·포지션
숫자가 없었다. 두 컬럼/테이블로 그 갭을 메운다:

- `account_snapshot_value`: 스냅샷 하나가 실제로 담는 잔고 숫자들(entity_type/
  entity_key/value) — reconciliation(FND-08)의 `EntitySnapshot.provider_value`가
  소비할 수 있는 것과 같은 (entity_type, entity_key, value) 모양이다. get_positions()는
  이 리프가 실제로 붙이는 Bitget/KIS 두 거래소 모두 spot 전용이라 항상 빈
  리스트를 반환하도록 그 어댑터들 자신이 이미 문서화해뒀다(account_mixin.py
  참조) — 그래서 여기 담기는 건 사실상 get_balance() 결과뿐이다.
- `credential_binding.scope_verified`: 거래소 API에는 AIOS의 READ_BALANCE/
  READ_POSITION/READ_ACTIVITY 권한 분류로 직접 매핑되는 조회 수단이 없다
  (Bitget get_account_info()는 raw dict라 안정적으로 파싱할 근거가 없음) —
  실 provider 경로는 이 플래그를 false로 정직하게 남긴다(연결 자체는
  차단하지 않는다, PM 지시 "미검증으로 정직 표기"). 기존 행(전부 fake
  provider로 만들어졌던 것)은 DEFAULT true로 백필한다 — 그 경로는 실제로
  시뮬레이션이 "검증됨"을 가정하고 만든 값이었으므로.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5ed4921f9873"
down_revision: str | None = "b3f7e0c1a4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE credential_binding ADD COLUMN scope_verified BOOLEAN NOT NULL DEFAULT true"
    )
    op.execute(
        """
        CREATE TABLE account_snapshot_value (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            snapshot_id  UUID NOT NULL REFERENCES account_snapshot(id),
            entity_type  VARCHAR(30) NOT NULL,
            entity_key   VARCHAR(100) NOT NULL,
            value        NUMERIC(30,10) NOT NULL,
            UNIQUE (snapshot_id, entity_type, entity_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_account_snapshot_value_snapshot_id "
        "ON account_snapshot_value (snapshot_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE account_snapshot_value")
    op.execute("ALTER TABLE credential_binding DROP COLUMN scope_verified")
