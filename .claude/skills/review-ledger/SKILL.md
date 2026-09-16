---
name: review-ledger
description: 복식부기 머니 원장, 포지션 저널, Decimal 정밀도, 멱등 포스팅, WORM 축 검토 체크리스트
paths:
  - "src/foundation/ledger/**"
  - "src/foundation/positions/**"
  - "src/foundation/market_data/**"
  - "src/db/migrations/versions/*ledger*"
  - "src/db/migrations/versions/*position*"
  - "src/db/migrations/versions/*worm*"
tier: [S]
axis: ledger
---

# review-ledger

## 적용 조건

머니 원장(`ledger/`), 포지션/PnL 저널(`positions/`), 이 둘에 걸린 마이그레이션을 건드릴 때
적용한다(tier S). `docs/specs/L4_market_data_positions_ledger_v1.0.md` §1(C1·C3)·§4.3·§4.4,
105번(쓰기 표준), INVARIANTS I-03·I-04·I-10이 규범이다.

## 체크리스트

1. 모든 분개(journal entry)는 복식부기다 — 한 트랜잭션 안에서 Σ차변 = Σ대변이 DB
   제약(CHECK 또는 트리거)으로 강제되고, 애플리케이션 계산에만 의존하지 않는다
   [근거: spec:L4_market_data_positions_ledger_v1.0.md#C1] [검사: 후보]
2. 저널 테이블은 append-only다 — UPDATE/DELETE 권한이 role 분리로 소유자 계정에도
   차단되어 있고(WORM), 소유자 role 접속만으로 REVOKE를 무력화할 수 없다
   [근거: spec:L4_market_data_positions_ledger_v1.0.md#C1] [검사: 후보]
3. 저널 각 행은 이전 행 해시를 포함하는 해시 체인을 갖고, `verify_chain()`류 검증 함수가
   체인 단절을 탐지한다 [근거: spec:L4_market_data_positions_ledger_v1.0.md#journal_rules]
   [검사: 후보]
4. 모든 포스팅은 비즈니스 사건(`event_type`, `event_ref`)에 추적되고, 멱등키
   `{event_type}:{event_ref}` 형식으로 재전송 시 같은 사건 → 같은 분개, 다른 payload →
   digest 비교 후 거부(409류)한다 [근거: spec:L4_market_data_positions_ledger_v1.0.md#C3, I-03]
   [검사: 후보]
5. 체결 기록의 멱등키는 `fill:{order_id}:{fill_seq}` 형식을 그대로 쓴다 — 새 멱등키 체계를
   임의로 발명하지 않는다 [근거: spec:L4_market_data_positions_ledger_v1.0.md#3.2]
   [검사: 후보]
6. 모든 포스팅이 `post_entry`류 단일 경로(멱등 lookup → `lines_for` → 잔액 검증 →
   FOR UPDATE → apply → journal append → 잔액 갱신 → 감사)를 거친다 — 이 경로를 우회해
   잔액이나 저널을 직접 갱신하는 새 코드 경로가 없다
   [근거: spec:L4_market_data_positions_ledger_v1.0.md#post_entry] [검사: 후보]
7. 금액·수량 필드는 전부 `Decimal`이고, DB 컬럼도 `NUMERIC`/`DECIMAL`이다 — `float`나
   부동소수 캐스팅이 섞여 있지 않다 [근거: 11_implementation_rules_v1.2.md]
   [검사: scripts/check_consistency.py::check_money_float]
8. `position_key`(포지션 식별자) 생성이 여러 곳에 흩어지지 않고 단일 중앙 생성자를 거친다
   — 문자열을 직접 조립하는 새 호출부가 없다 [근거: FA-0d]
   [검사: scripts/check_position_key_central.py]
9. 포지션 저널 불변조건(시퀀스 연속, 현물 수량 비음수, 멱등키 형식)이 append 전에
   검증되고, 위반 시 append 자체가 거부된다(재조정용 사후 UPDATE가 아니다)
   [근거: spec:L4_market_data_positions_ledger_v1.0.md#4.3] [검사: 후보]
10. datetime 필드는 전부 tz-aware UTC다 — naive datetime이 저널·원장 경계를 넘지 않는다
    [근거: 01_data_models_v1.4.md §1.7] [검사: scripts/check_consistency.py::check_naive_datetime]
11. 백필/소급 마이그레이션이 실패하면 트랜잭션 전체가 롤백된다(부분 백필 후 방치가 없다) —
    `alembic upgrade`가 서브프로세스 실패로 중단돼도 절반만 채워진 행이 남지 않는다
    [근거: FA-0d] [검사: 후보]
12. correction/restatement(정정 분개)은 원본 행을 고치지 않고 반대 분개 + 신규 분개를
    append하는 방식으로만 이뤄진다(WORM 유지) [근거: FA-11] [검사: 후보]

## 반례

### 반례 1 — 잔액을 저널 우회 직접 UPDATE

```python
# BAD: post_entry 경로를 건너뛰고 잔액만 갱신 — 저널에 대응 분개가 없어 감사 불가능.
async def credit_bonus(conn, account_id: UUID, amount: Decimal) -> None:
    await conn.execute(
        "UPDATE ledger_balance SET balance = balance + :amt WHERE account_id = :id",
        {"amt": amount, "id": account_id},
    )

# GOOD: post_entry를 통해 분개+저널+잔액을 한 트랜잭션으로.
async def credit_bonus(conn, event: BonusGrantedEvent, *, journal, balances, audit) -> None:
    await post_entry(conn, event, journal=journal, balances=balances, audit=audit)
```

### 반례 2 — 멱등키 없이 재전송 시 중복 분개

```python
# BAD: 같은 체결이 재전송되면 분개가 두 번 append된다(네트워크 재시도가 곧 이중 지출).
async def record_fill(conn, order_id: UUID, fill_seq: int, amount: Decimal) -> None:
    await journal.append(conn, JournalEntry(order_id=order_id, amount=amount))

# GOOD: 멱등키로 기존 분개를 먼저 조회하고, digest가 같으면 그 결과를 그대로 반환.
async def record_fill(conn, order_id: UUID, fill_seq: int, amount: Decimal) -> JournalEntryView:
    key = f"fill:{order_id}:{fill_seq}"
    existing = await journal.find_by_idempotency_key(conn, key)
    if existing is not None:
        assert_same_digest(existing, amount)
        return existing
    return await journal.append(conn, JournalEntry(idempotency_key=key, amount=amount))
```

## 이 축에서 났던 사고

- `legacy_wallet_bridge`가 커넥션 풀 placeholder를 실제 값처럼 취급해 잘못된 분개가
  나갈 수 있던 결함을 sentinel 값으로 대체해 fail-closed화 (git:289dada4)
- `audit_regressions` 스캐너가 `ledger_balance_raw_seed`(잔액을 원장 우회로 직접 시딩하는
  코드 패턴)를 감지해 회귀를 해소 (git:dc58d885)
- FA-4 소급 마이그레이션에서 `pos_journal`·`ledger_journal_entry`에 WORM 트리거 없이
  백필하면 불변식이 깨진다는 지적으로 `test_migration_fa4_worm_no_backfill.py`를 추가
  (git:9c9ff68)
- FA-0d 재대조: `pos_snapshot.position_key`를 여러 호출부가 각자 문자열로 조립해 표기
  불일치가 나던 결함을 중앙 생성자 + 정적 검사(`check_position_key_central.py`)로 봉쇄
  (git:278f6227)
