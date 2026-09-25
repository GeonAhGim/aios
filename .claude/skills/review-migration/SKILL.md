---
name: review-migration
description: alembic 마이그레이션 — 단일 head, downgrade, expand/contract, fail-closed 백필 축 검토 체크리스트
paths:
  - "src/db/migrations/versions/**"
  - "alembic.ini"
tier: [S]
axis: migration
---

# review-migration

## 적용 조건

`src/db/migrations/versions/`에 새 리비전을 추가하거나 기존 리비전을 수정할 때 적용한다
(tier S — 마이그레이션은 항상 parent 결정 필요, PROTOCOL.md). PLT-38 결정,
`docs/audit/DEPTH_FA.md`, `docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md`가 규범이다.

## 체크리스트

1. 새 리비전 추가 후 `down_revision` 체인을 따라간 head가 정확히 1개다(리비전 2개 이상이
   같은 `down_revision`을 가리켜 head가 여러 개로 갈라지지 않는다)
   [근거: PLT-38] [검사: scripts/check_migration_chain.py]
2. `down_revision`이 실제로 존재하는 리비전 id를 가리킨다(오타·삭제된 리비전 참조로 체인이
   끊기지 않는다) [근거: PLT-38] [검사: scripts/check_migration_chain.py]
3. `down_revision` 참조에 순환이 없다 [근거: PLT-38] [검사: scripts/check_migration_chain.py]
4. `downgrade()`가 `pass`나 `raise NotImplementedError`로 비어 있지 않고 `upgrade()`의 실제
   역연산을 구현한다 — 컬럼 추가엔 컬럼 제거, 제약 추가엔 제약 제거
   [근거: 04_db_schema_v1.7.md] [검사: 후보]
5. 컬럼 추가는 expand(nullable 허용 또는 기본값 포함)로 시작하고, contract(NOT NULL 강제·
   컬럼 제거)는 백필이 끝난 뒤 별도 리비전으로 분리한다 — 한 리비전 안에서 컬럼 추가와
   NOT NULL 강제를 동시에 하지 않는다(배포 중 구버전 앱 호환 파괴)
   [근거: ADR-2026-09-10-C] [검사: 후보]
6. 새 FK 제약을 추가하는 리비전은 기존 데이터가 제약을 위반하는 행이 있는지 사전에
   점검하거나, 백필로 먼저 정합성을 맞춘 뒤 제약을 건다(제약 추가만 하고 위반 행 처리가
   없으면 프로덕션 적용이 실패한다) [근거: FA-3, FA-4] [검사: 후보]
7. 백필이 포함된 마이그레이션은 실패 시 트랜잭션 전체가 롤백되는 fail-closed다 — 부분
   백필 후 그대로 방치되는 경로가 없다(서브프로세스 실패로 중단돼도 절반만 채운 상태로
   커밋되지 않는다) [근거: FA-0d] [검사: 후보]
8. WORM 대상 테이블(`audit_log`, `ledger_journal_entry`, `pos_journal` 등)에 대한 백필은
   append-only 트리거를 우회하지 않는다 — 트리거를 임시로 DROP했다가 마이그레이션 끝에
   복구하는 패턴이 없다(그 창 동안 append-only 보장이 깨진다)
   [근거: spec:L4_market_data_positions_ledger_v1.0.md#C1] [검사: 후보]
9. `position_key`처럼 중앙 생성자가 있는 식별자를 백필할 때도 그 생성자를 재사용한다 —
   마이그레이션 안에서 별도 문자열 조립 로직을 새로 만들지 않는다
   [근거: FA-0d] [검사: scripts/check_position_key_central.py]
10. 리비전 파일이 CI가 사용하는 실제 DB 준비 단계(`setup_test_db.py`, 테스트 conftest)를
    깨뜨리지 않는다 — fail-closed 가드가 리비전 자체 로직뿐 아니라 CI DB 부트스트랩
    경로에서도 통과하는지 확인한다 [근거: FA-0d] [검사: 후보]
11. 마이그레이션이 건드리는 테이블에 동시 실행 경합 테스트(여러 워커가 동시에 같은 백필을
    트리거해도 gapless 시퀀스·UNIQUE 위반이 안 남)가 있다
    [근거: FA-0d] [검사: 후보]
12. `revision`/`down_revision`이 함수 호출이나 변수 참조가 아니라 모듈 레벨 문자열
    리터럴이다(정적 검사가 AST만으로 체인을 추출할 수 있어야 한다)
    [근거: PLT-38] [검사: scripts/check_migration_chain.py]

## 반례

### 반례 1 — 여러 head를 만드는 병합 리비전 누락

```python
# BAD: 두 워커가 병렬로 리비전을 만들어 같은 down_revision을 가리키면 head가 2개가 된다.
# revision A: down_revision = "base123"
# revision B: down_revision = "base123"   # 병합 리비전 없이 그대로 커밋
# -> alembic upgrade head 가 어느 head인지 모호해진다.

# GOOD: 병합 리비전을 추가해 단일 head로 합친다.
# revision = "merge_ab"; down_revision = ("A", "B")
```

### 반례 2 — 컬럼 추가와 NOT NULL 강제를 한 리비전에서 동시에

```python
def upgrade() -> None:
    # BAD: 배포 도중 구버전 앱이 이 컬럼 없이 INSERT하면 즉시 실패한다.
    op.add_column("orders", sa.Column("portfolio_id", sa.UUID(), nullable=False))

# GOOD: expand(nullable) -> 백필 -> 별도 리비전에서 contract(NOT NULL).
def upgrade() -> None:
    op.add_column("orders", sa.Column("portfolio_id", sa.UUID(), nullable=True))
```

## 이 축에서 났던 사고

- task-2099의 alembic 병합 리비전이 빠져 head가 갈라졌던 사고를 재발 방지 테스트로
  고정 (git:ea4fda65)
- FA-2a: `legal_entity.tenant_id`가 존재하지 않는 `users` 테이블을 참조하도록 잘못
  마이그레이션되어 재도입 방지 스캐너를 추가 (git:09d9ed5)
- FA-4: `pos_journal`·`ledger_journal_entry`에 컬럼을 백필하면서 WORM 불변식이 깨질 수
  있다는 지적으로 `test_migration_fa4_worm_no_backfill.py`를 추가 (git:9c9ff68)
- FA-0d 재대조: fail-closed 백필 가드가 CI의 실제 Postgres DB 준비 단계 자체를 깨뜨리는
  `ObjectInUseError` 근본 원인을 수정 (git:46f9d027)
