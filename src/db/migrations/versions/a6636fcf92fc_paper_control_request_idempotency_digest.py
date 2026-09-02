"""paper_control_request_idempotency_digest — FND-07 PAP-006 갭 보강

Revision ID: a6636fcf92fc
Revises: b7e2c4d9f1a6
Create Date: 2026-09-02 23:40:00.000000

Spec: AIOSproject 77_paper_execution_control_l3_build_and_operational_specification_v1.0.md
PAP-006 "duplicate command is idempotent".

전수감사(agent-platform-12, docs/FULL_AUDIT_2026-09-02.md §2-B) 발견 —
`request_deployment()`가 매번 새 `deployment_id = uuid4()`를 생성한 뒤 곧장
insert해, `deployment_command.idempotency_key`의 UNIQUE 제약이
`(deployment_id, idempotency_key)` 복합키라 애초에 매치될 대상(같은
deployment_id)이 없다 — 같은 idempotency_key로 REQUEST를 재시도하면
매번 새 deployment가 만들어졌다(중복 배포 생성, 진짜 idempotency 아님).

이 컬럼들은 그 결함을 막는다:
- `request_idempotency_key`: REQUEST 호출자가 보낸 키. `(tenant_id,
  request_idempotency_key)`에 UNIQUE를 걸어 같은 tenant의 같은 키로
  두 번째 INSERT가 경합하면 DB가 직접 막는다(ON CONFLICT DO NOTHING +
  재조회 — connections CON-006과 같은 패턴, `check-then-insert`의 TOCTOU를
  피한다).
- `request_digest`: 같은 키로 왔지만 실제 요청 내용(package_ref 등)이
  다르면 "그때 그 응답을 재사용"이 아니라 명백한 오류다 — 재조회 후 저장된
  digest와 비교해 다르면 409로 거부한다(진짜 idempotency는 "같은 요청의
  재시도"만 캐시해야 한다 — 다른 요청에 같은 키를 잘못 재사용한 클라이언트
  버그를 조용히 삼키면 안 된다).

기존 행은 전부 이 마이그레이션 이전에 생성됐으므로 원본 요청 내용을 복원할
수 없다 — 컬럼을 NOT NULL로 걸 수 없는 이유. 대신 부분 UNIQUE 인덱스로
NULL은 유니크 검사에서 자연히 제외한다(Postgres 기본 동작, `WHERE` 없이도
NULL != NULL). 새 REQUEST부터는 애플리케이션이 항상 두 값을 채운다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a6636fcf92fc"
down_revision: str | None = "b7e2c4d9f1a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE paper_deployment "
        "ADD COLUMN request_idempotency_key TEXT, "
        "ADD COLUMN request_digest TEXT"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_paper_deployment_tenant_request_key "
        "ON paper_deployment (tenant_id, request_idempotency_key)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ux_paper_deployment_tenant_request_key")
    op.execute(
        "ALTER TABLE paper_deployment "
        "DROP COLUMN request_idempotency_key, "
        "DROP COLUMN request_digest"
    )
