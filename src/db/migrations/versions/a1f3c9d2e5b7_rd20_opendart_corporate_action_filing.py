"""RD-20 — md_corporate_action_filing(append-only) + 미처리 공시 큐.

Revision ID: a1f3c9d2e5b7
Revises: bd931e3aa0d4
Create Date: 2026-09-07

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20,
ADR-2026-09-06-H D3(국내 기업행위는 전자공시 원본에서 자체 구축).

`md_corporate_action_filing`은 `md_corporate_action`(LA-12, task-451,
`(instrument_id, action_type, ex_date)` UNIQUE·"지금 유효한 값 하나")과는
별개 테이블이다. 이 테이블은 정정 공시를 포함한 전체 접수 이력을
append-only로 쌓는다 — 멱등키는 DART 접수번호(`source_ref`) 하나뿐이고,
`(instrument_id, action_type, ex_date)`에는 의도적으로 UNIQUE를 걸지 않는다
(정정이 같은 키로 여러 행을 갖는 것이 정상이다). `aios_app`에 UPDATE·DELETE
권한을 주지 않아 애플리케이션 계층의 실수·버그로도 기존 행을 못 고치게
DB 권한 자체로 append-only를 강제한다(코드 리뷰가 아니라 배선으로 증명).

`research_opendart_unprocessed_filing`은 파싱 실패·소스 계약 거부 공시가
조용히 사라지지 않고 남는 큐다. `raw_payload`는 `OpenDartFiling`의 구조화
필드만 담고(공시 원문 텍스트 없음) DC-27 재배포 제한을 위반하지 않는다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1f3c9d2e5b7"
down_revision: str | Sequence[str] | None = "bd931e3aa0d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE md_corporate_action_filing (
            filing_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            instrument_id       UUID NOT NULL REFERENCES md_instrument(instrument_id),
            action_type         VARCHAR(20) NOT NULL
                CHECK (action_type IN ('SPLIT','REVERSE_SPLIT','CASH_DIVIDEND','MERGER')),
            ex_date             DATE NOT NULL,
            ratio               NUMERIC(20,10) NOT NULL CHECK (ratio > 0),
            cash_amount         NUMERIC(20,10),
            source_ref          VARCHAR(200) NOT NULL,
            known_at            TIMESTAMPTZ NOT NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (source_ref)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_md_corporate_action_filing_instrument "
        "ON md_corporate_action_filing (instrument_id, action_type, ex_date, known_at)"
    )
    op.execute(
        f"GRANT SELECT, INSERT ON md_corporate_action_filing TO {_APP_ROLE}"
    )

    op.execute(
        """
        CREATE TABLE research_opendart_unprocessed_filing (
            id              BIGSERIAL PRIMARY KEY,
            source_id       VARCHAR(64) NOT NULL,
            raw_payload     JSONB NOT NULL,
            reason          TEXT NOT NULL,
            received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            resolved        BOOLEAN NOT NULL DEFAULT FALSE,
            resolved_at     TIMESTAMPTZ
        )
        """
    )
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE ON research_opendart_unprocessed_filing TO {_APP_ROLE}"
    )
    op.execute(
        f"GRANT USAGE, SELECT ON research_opendart_unprocessed_filing_id_seq TO {_APP_ROLE}"
    )


def downgrade() -> None:
    op.execute("DROP TABLE research_opendart_unprocessed_filing")
    op.execute("DROP TABLE md_corporate_action_filing")
