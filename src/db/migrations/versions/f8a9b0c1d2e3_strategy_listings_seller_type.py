"""strategy_listings.seller_type — USER/PLATFORM 판매자 구분

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-08-29 00:00:01.000000

Spec: ADR-2026-08-29-wallet-marketplace-dual-seller-strategy-authoring.md §2
— 마켓플레이스가 유저간 판매(P2P)뿐 아니라 플랫폼 직접판매(B2C)도
지원해야 하며, 두 판매자 유형은 동일한 커미션 구조로 취급한다는 사용자
결정. seller_user_id는 PLATFORM 리스팅도 그대로 채운다 —
wallet_service.PLATFORM_HOUSE_USER_ID(하우스 계정)를 넣어 FK 제약을
그대로 충족하고 정산 로직(purchase_service.py)을 분기 없이 재사용한다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8a9b0c1d2e3"
down_revision: str | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE strategy_listings ADD COLUMN seller_type VARCHAR(20) "
        "NOT NULL DEFAULT 'USER' CHECK (seller_type IN ('USER','PLATFORM'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE strategy_listings DROP COLUMN seller_type")
