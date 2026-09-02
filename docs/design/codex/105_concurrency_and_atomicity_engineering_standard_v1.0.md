# Concurrency and Atomicity Engineering Standard v1.0

> 상태: **Mandatory cross-cutting standard.** 72번(L3 depth standard)의 §5 test depth를
> 구체화하고, 73~81번(Foundation L3) 및 이후 모든 L3 문서가 "동시성/concurrency"
> 섹션을 쓸 때 반드시 따라야 할 실제 패턴·헬퍼 시그니처·테스트 형식을 정의한다.
>
> 작성자: Claude Code(DevEngine 세션과는 별도 — `C:\aios\mihwa-aios`에서 직접
> 코드를 수정하는 구현 세션). 근거: 아래 §1은 추측이 아니라 이 세션이
> `mihwa-aios`에서 실제로 찾아 고친 결함의 목록이다.
>
> 작성일: 2026-09-02

---

## 1. 이 표준이 존재하는 이유 — 실제 재발 이력

`mihwa-aios`에서 "상태를 SELECT로 읽고 검증한 뒤, 그 상태 조건 없이 UPDATE한다"는
같은 결함 클래스가 최소 6개 서비스에서 **각각 독립적으로 발견되고 고쳐졌다**:

| # | 파일 | 레드팀 발견 | 실제 위험 시나리오 |
|---|---|---|---|
| 05 | `dispute_resolution_service.py` | #05 | 두 관리자가 거의 동시에 같은 분쟁을 다르게 처리 → 나중 커밋이 조용히 덮어씀 |
| 09 | `portfolio_service.py` | #09 | 동시 재조정 요청이 서로의 배분을 무시하고 덮어씀 |
| 16 | `verification_service.py` | #05/#16 | 두 검증담당자가 같은 리스팅을 하나는 승인·하나는 반려 → 나중 커밋 승리 |
| 17 | `strategy_builder_service.py` | #17 | 반려된 전략이 자동 파이프라인의 다음 단계 전이로 조용히 되살아남 |
| — | `wallet_service.py` | (동일 클래스) | 중복 충전 승인으로 지갑 잔액 이중 반영 |
| — | 그 외 최소 13개 파일 | `grep -rl "RETURNING" src/services/` | 각자 독립적으로 같은 패턴을 손으로 재발명 |

**핵심 관찰**: 이 결함은 한 번 배운다고 재발이 멈추지 않는다 — 서로 다른 세션(사람 +
여러 AI 에이전트)이 서로 다른 파일에서 **매번 처음 발견하는 것처럼** 고쳤다. 이는
개별 코드 리뷰의 실패가 아니라 **공유 규칙과 재사용 가능한 헬퍼의 부재**다. 이
표준의 목적은 다음 서비스를 만들 때 이 결함이 애초에 발생할 수 없게 만드는 것이다.

이는 `103_enterprise_architecture_full_audit_and_remediation_brief_v1.0.md`의
**P0-03(주문 멱등성·동시성·정산의 원자성)**, `82_red_team_architecture_assessment
_v1.0.md`의 **RT-03(kill switch/TOCTOU)**·**RT-07(idempotency key)**과 정확히 같은
문제의식이다 — 이 문서는 그 원칙을 실제 코드 레벨 규칙으로 내려받는다.

---

## 2. 규칙 1 — "읽은 상태"는 항상 쓰기의 조건이다

어떤 command handler든 다음 순서를 밟는다면:

```text
1. SELECT ... 로 현재 상태(status/lifecycle_status/mfa_enabled 등)를 읽는다
2. 그 값으로 비즈니스 규칙을 검증한다 (전이 가능한가? 권한이 있는가?)
3. UPDATE ... 로 쓴다
```

**3번 UPDATE는 반드시 1번에서 읽은 값을 `WHERE` 절 조건으로 포함하고, `RETURNING`
으로 실제 변경된 행이 있었는지 확인해야 한다.** 조건 없는 UPDATE는 1번과 3번 사이의
시간 간격 동안 다른 트랜잭션이 같은 행을 먼저 바꿨을 가능성을 무시한다.

### 2.1 표준 형태

```sql
UPDATE <table>
SET <target_column> = $new_value, updated_at = now()
WHERE <id_column> = $id
  AND <state_column> = $expected_state   -- 1번에서 읽은 값 그대로
RETURNING <id_column>
```

```python
row = await conn.fetchrow(sql, id, new_value, expected_state)
if row is None:
    raise ConcurrencyConflictError(
        "다른 요청이 먼저 처리했습니다(동시 처리 충돌) — 다시 조회 후 시도하세요."
    )
```

`row is None`은 "그 사이 상태가 바뀌었다"는 뜻이지 "행이 없다"는 뜻이 아니다 — 존재
여부는 1번 SELECT에서 이미 확인했으므로, 여기서의 실패는 항상 동시성 충돌로
해석한다.

### 2.2 예외 인정 기준

이 조건을 생략해도 되는 경우는 다음 뿐이다:

- 대상 컬럼이 **append-only**이고(예: audit 이벤트, 새 row INSERT) 같은 행을 다시
  쓰지 않는 경우.
- UPDATE가 **단조 증가/감소만 허용**하고 DB 자체의 CHECK 제약이 역행을 막는 경우
  (예: `WHERE version < $new_version`도 이 표준의 한 형태로 인정된다 — 핵심은
  "조건 없는 UPDATE가 없다"는 것이지 특정 컬럼명이 아니다).
- 단일 소유자(single-writer)임이 스키마 레벨에서 보장된 경우(예: `tenant_id +
  UNIQUE` 제약으로 애초에 경합 대상이 하나뿐).

"지금 당장 이 경로를 두 곳에서 동시에 부를 방법이 없어 보인다"는 이유로 생략하지
않는다 — `strategy_builder_service.py`(#17)가 정확히 이 실수였다: 발견 당시 라우터
미배선이라 "당장은 안전"했지만, 나중에 배선되는 순간 같은 결함이 재발할 뻔했다.
**호출 경로의 현재 상태가 아니라 함수 자체의 계약으로 안전성을 보장한다.**

---

## 3. 규칙 2 — 재사용 가능한 리포지토리 헬퍼를 우선 사용한다

19개 서비스가 각자 손으로 이 패턴을 재구현한 것 자체가 문제다. 새 bounded
context(`src/foundation/<context>/`, 34/35/71번 참조)는 반드시 공유 헬퍼를 통해서만
조건부 쓰기를 수행한다.

```python
# src/core/db/conditional_write.py (신설 대상 — 아직 없음, FND-01 착수 시 우선 구현)

class ConcurrencyConflictError(Exception):
    """읽은 상태와 쓰려는 시점의 실제 상태가 달랐다. 호출자는 재조회 후 재시도하거나
    사용자에게 409로 노출한다 — 이 예외를 삼키지 않는다."""


async def conditional_update(
    conn: asyncpg.Connection,
    *,
    table: str,
    id_column: str,
    id_value: Any,
    expected_state_column: str,
    expected_state_value: Any,
    set_values: dict[str, Any],
    returning: str = "*",
) -> asyncpg.Record:
    """RETURNING이 빈 결과면 ConcurrencyConflictError를 던진다.

    `set_values`의 컬럼명은 SQL 인젝션 방지를 위해 반드시 화이트리스트 상수를
    통해서만 전달한다(호출자가 임의 문자열을 넘기지 않는다).
    """
    ...
```

L3 문서(73~81 및 이후 모든 도메인 L3)는 §7(동시성) 섹션에서 이 헬퍼를 쓰는지, 아니면
왜 예외 사유(§2.2)에 해당해 직접 SQL을 쓰는지 명시한다. 이 헬퍼가 아직 코드베이스에
없으므로, **FND-01(71번 문서) 착수 시 첫 PR에서 함께 만든다** — 이후 모든
Foundation write path가 처음부터 이걸 쓰게 한다. 기존 19개 서비스의 손코딩된
인스턴스는 강제 마이그레이션 대상은 아니지만(동작은 이미 올바름), 그 중 하나를
건드리는 다음 PR은 이 헬퍼로 옮기는 걸 권장한다.

---

## 4. 규칙 3 — 모든 동시성 수정에는 실제 경합을 재현하는 회귀 테스트가 따른다

"조건을 추가했다"는 것만으로는 증명이 안 된다. 두 가지 형태 중 하나를 반드시
포함한다(72번 §5 test depth의 "concurrency" 항목을 구체화):

### 4.1 형태 A — `asyncio.gather`로 실제 경합 재현 (선호)

```python
async def test_concurrent_<action>_only_one_succeeds(service, pool):
    ...  # 초기 상태 하나 준비
    results = await asyncio.gather(
        service.<method>(...),
        service.<method>(...),
        return_exceptions=True,
    )
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, ConcurrencyConflictError)]
    assert len(successes) == 1
    assert len(failures) == 1
```

`strategy_builder_service.py`의
`test_concurrent_transitions_only_one_succeeds`(`tests/integration/
test_strategy_builder_service.py`)가 이 형태의 실제 예시다.

### 4.2 형태 B — 선행 변경을 직접 주입해 stale read를 재현

서비스 메서드 자체가 한 번의 원자적 호출이라 `gather`로 경합을 못 만들 때는, 테스트가
호출 *전에* DB 행을 직접 다른 상태로 바꿔둔 뒤 호출이 실패하는지 확인한다.

```python
async def test_rejects_when_state_changed_before_write(service, pool):
    ...  # 대상 준비
    async with pool.acquire() as conn:
        await conn.execute("UPDATE <table> SET <state_column> = $2 WHERE ...", id, other_state)
    with pytest.raises(ConcurrencyConflictError):
        await service.<method>(...)
```

**금지되는 거짓 테스트**: 새 상태를 읽지 않고 그냥 두 번 호출해서 두 번째가 "이미
처리됨" 같은 비즈니스 규칙 위반으로 실패하는 것을 동시성 테스트로 위장하지 않는다
(예: 이미 REJECTED인 걸 다시 REJECT하려다 실패하는 건 동시성 검증이 아니라 상태
머신 검증이다 — 두 개는 별도 테스트다).

---

## 5. 규칙 4 — Kill switch / stop 계열은 이 표준을 넘어선 "in-flight" 문제를 별도로 다룬다

`82_red_team_architecture_assessment_v1.0.md` RT-03이 지적한 대로, DB 행 잠금만으로는
"이미 provider에 나간 HTTP 요청"을 되돌릴 수 없다. 이 표준(§2~4)은 **DB 내부의 상태
경합**만 다룬다. Provider 제출 이후의 `NOT_SENT/SENT_UNKNOWN/ACKNOWLEDGED/
RECONCILING` 같은 외부 시스템 경합은 이 문서의 범위 밖이며, RT-03이 요구하는 별도
"execution linearizability ADR" 대상이다. Kill switch·주문 제출 코드를 이 표준만으로
안전하다고 판단하지 않는다.

---

## 6. 코드 리뷰 체크리스트

새 write path를 리뷰할 때 다음을 확인한다:

1. 이 UPDATE/DELETE 앞에 같은 행을 읽는 SELECT가 있는가?
2. 있다면, 그 UPDATE의 `WHERE`에 방금 읽은 상태 컬럼이 조건으로 들어가 있는가?
3. `RETURNING`(또는 driver의 rowcount)으로 "조건이 안 맞아 0행 변경"을 명시적으로
   구분해 처리하는가, 아니면 무시하고 성공한 것처럼 흘러가는가?
4. §4 형태 A/B 중 하나에 해당하는 테스트가 같은 PR에 있는가?
5. 이 경로가 "지금은 호출자가 하나뿐"이라는 이유로 조건을 생략하지 않았는가(§2.2
   예외 기준을 실제로 충족하는가)?

이 다섯 질문에 하나라도 "아니오"이면 merge하지 않는다.
