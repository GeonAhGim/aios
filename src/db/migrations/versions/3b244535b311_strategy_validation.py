"""strategy_validation — FND-04 실제 계산 연결 (backtest 검증)

Revision ID: 3b244535b311
Revises: a1f3c9d6b8e2
Create Date: 2026-09-02 00:00:00.000000

Spec: AIOSproject 46_strategy_package_and_validation_specification_v1.0.md,
76_strategy_package_validation_l3_build_and_operational_specification_v1.0.md,
109_backtest_simulation_engine_l3_build_and_operational_specification_v1.0.md.

방향 결정(사용자 확인, 2026-09-02): 76번 문서가 제안하는 독립된
`foundation/strategy_packages`(strategy_draft/strategy_artifact/
strategy_package, CSM 컴파일, PAPER_ELIGIBLE 등)를 병렬로 새로 만들지
않는다 — 이미 `strategies` 테이블에 9.9 절대원칙으로 강제되는 생애주기
(GENERATED→BACKTESTING→VALIDATING→...)가 있고, `strategy_builder.py`
라우터 자체가 "백테스트/검증 파이프라인이 생기면 내부 호출 경로로
transition_lifecycle()에 연결하라"고 이미 명시해뒀다(편차 3 주석). 이
리프는 그 파이프라인의 "Backtest" 체크 하나를 실제로 만들어 그 자리에
끼운다 — 76번 §3의 6개 체크(point-in-time/backtest/OOS/robustness/
stress/failure-conditions) 중 지금 FND-10(백테스트 엔진)이 실제로 계산할
수 있는 "Backtest" 하나만 구현한다.

`strategy_validation_run.check_type`을 문자열로 열어두는 건 나중에
OOS/robustness/stress 체크가 생겨도 이 테이블을 재사용하기 위함이다
(같은 강도로 "validation_run/validation_result" 이름·상태를 76번 §1과
맞춰뒀다 — 나중에 진짜 FND-04 패키지 레이어가 필요해지면 그때 흡수).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3b244535b311"
down_revision: str | None = "a1f3c9d6b8e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE strategy_validation_run (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            strategy_id          VARCHAR(100) NOT NULL,
            strategy_version     VARCHAR(20) NOT NULL,
            check_type           VARCHAR(30) NOT NULL DEFAULT 'backtest',
            input_snapshot_hash  VARCHAR(64) NOT NULL,
            cost_model           JSONB NOT NULL,
            warmup_bars          INT NOT NULL,
            periods_per_year     INT NOT NULL,
            initial_equity       NUMERIC(20,8) NOT NULL,
            state                VARCHAR(20) NOT NULL DEFAULT 'QUEUED'
                CHECK (state IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')),
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at         TIMESTAMPTZ,
            FOREIGN KEY (strategy_id, strategy_version)
                REFERENCES strategies (strategy_id, version),
            -- 76번 §1 "input/config pinned before queue" + STR-001/STR-007 재현성·
            -- 멱등성 — 같은 전략의 같은 정확한 입력 조합은 재실행이 아니라 기존
            -- 결과를 그대로 반환한다(application 계층이 이 제약을 캐치해 처리).
            UNIQUE (strategy_id, strategy_version, check_type, input_snapshot_hash)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_strategy_validation_run_strategy "
        "ON strategy_validation_run (strategy_id, strategy_version)"
    )
    op.execute(
        """
        CREATE TABLE strategy_validation_result (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id            UUID NOT NULL UNIQUE REFERENCES strategy_validation_run(id),
            outcome           VARCHAR(30) NOT NULL
                CHECK (outcome IN ('PASS', 'FAIL', 'PASS_WITH_OBLIGATIONS')),
            metrics           JSONB NOT NULL,
            warnings          TEXT[] NOT NULL DEFAULT '{}',
            hard_fail_reasons TEXT[] NOT NULL DEFAULT '{}',
            obligations       TEXT[] NOT NULL DEFAULT '{}',
            result_hash       VARCHAR(64) NOT NULL,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # 79번 §1과 동일 원칙 — 검증 결과도 append-only 감사 대상이다.
    op.execute("REVOKE UPDATE, DELETE ON strategy_validation_result FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP TABLE strategy_validation_result")
    op.execute("DROP TABLE strategy_validation_run")
