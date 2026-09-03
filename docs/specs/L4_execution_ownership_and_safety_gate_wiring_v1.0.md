# L4 — 실행 소유권(리스/펜싱) · 안전 게이트 배선 구현 명세 v1.0

> **문서 성격: 연구 산출물(Research Output) — 작업지시서 아님.**
> 이 문서는 Claude/Fable(AIOS Codex-Fable 교차검토 체계의 "내부 아키텍처 감사·red-team" 역할,
> [`AIOS_Codex_Fable_CrossReview_Research_Direction_Record_2026-09-03.md`](../../../brainstorm/AIOS_Codex_Fable_CrossReview_Research_Direction_Record_2026-09-03.md) §6)가
> 오픈소스 교차검증([`AIOS_OSS_DeepDive_v2`](../../../brainstorm/AIOS_OSS_DeepDive_v2_CodeLevel_CrossVerification_2026-09-03.md))과
> 코드 감사로 도출한 **제안 설계**다. Claude/Fable은 `aios` 프로덕션 코드를 직접 수정하지
> 않는다 — 구현은 별도 담당(PM이 배정하는 워커)의 몫이다. PM/Chief Architect가 이 문서를
> 검토하고 `status: Approved`로 바꾼 뒤 §9 리프를 실제 `pm/tasks/*.json`으로 배정하기 전까지는
> 어떤 워커도 이 문서만 보고 착수하지 않는다. 기존 `L4_*.md`(승인 완료·리프 배정 진행 중인
> 작업지시서)와는 지위가 다르다 — 이 문서는 **후보**다.

## 0. 문서 메타

- **status**: Proposed (연구 산출물, 미승인)
- **owner role**: Fable(내부 아키텍처 감사) 작성 → Chief Architect/PM 검토 대기
- **supersedes**: 없음(신규)
- **depends on**: [`AIOS_Target_Architecture_Freeze_v0.1`](../../../brainstorm/AIOS_Target_Architecture_Freeze_v0.1_2026-09-03.md) §4(우선순위 1·2위:
  Durable Workflow & Ownership Plane, Policy Plane), [`AIOS_Registers_v1`](../../../brainstorm/AIOS_Registers_v1_Assumption_Contradiction_Invariant_Failure_2026-09-03.md)
  I-01·I-02(불변조건), P0-R1·P0-R2·P0-R3(연구 결정 기록 §8)
- **implemented by**: §2 모듈 분해 참조(전부 신규 또는 기존 파일 최소 수정)
- **verification evidence**: §8 테스트 계획 참조(전부 미작성 — 이 리프들의 산출물)
- **감사 근거**: 이 문서의 모든 "현재 상태" 서술은 2026-09-03 코드 감사로 직접 확인했다
  (`src/services/execution_loop/scheduler.py`, `src/services/background_loops.py`,
  `src/services/order_service/{gate,foundation_gate}.py`, `src/services/execution_loop/tick.py`
  — 읽기 전용, 이 감사 자체는 코드를 바꾸지 않았다).

## 1. 기관급 요구 (왜 지금 이 문제인가)

두 가지가 이미 코드로 완성돼 있는데 실행 경로에 연결되지 않아 무력화돼 있다 — "구현됨"과
"작동함"의 격차를 보여주는 정확한 사례다(연구 결정 기록 §10 원칙: IMPLEMENTED ≠ WIRED ≠
NON-BYPASSABLE ≠ EVIDENCE-PROVABLE).

1. **실행 소유권(P0-R1)**: `ExecutionLoopScheduler.list_runnable()`(`src/services/
   execution_loop/scheduler.py:86-92`)이 DB 소유권 조건 없이 `WHERE status='RUNNING' AND
   mode='PAPER'`만으로 실행 대상을 조회한다. 이 프로세스가 2개 이상 뜨면(블루/그린 배포,
   오토스케일 오조작 등) 같은 실행을 동시에 tick해 중복 주문을 낼 수 있다. `orders.
   client_order_id UNIQUE` 제약이 정확히 같은 밀리초의 중복은 막지만, 서로 다른 신호
   평가 결과(예: 두 인스턴스가 다른 시세를 봐서 다른 client_order_id를 만드는 경우)까지는
   막지 못한다.
2. **안전 게이트 배선(P0-R2, P0-R3)**: `make_foundation_pre_submit_gate(pool)`
   (`src/services/order_service/foundation_gate.py:37`)이 kill switch(GLOBAL/TENANT/
   ACCOUNT/PROVIDER 범위 safety control)를 검사해 DENY하는 로직을 완성해 뒀지만,
   `background_loops.py:146-151`의 `ExecutionLoopScheduler(...)` 생성 호출이 이 인자를
   전달하지 않아 기본값 `None`으로 흘러간다. `tick.py`의 `is_submission_allowed(None, ...)`
   경로는 게이트가 없으면 무조건 통과시킨다. **운영자가 kill switch를 ACTIVE로 올려도 실행
   루프의 신규 주문은 그대로 나간다.** `DataDistrustMonitor`도 같은 이유로 스케줄러에
   전달되지 않아 이상 시세 방어가 상시 꺼져 있다.

두 문제 모두 "새 기능을 만들어야 하는 문제"가 아니라 "이미 있는 것을 연결하지 않은 문제"다.
다만 그렇다고 사소한 문제는 아니다 — 자금이 걸린 시스템에서 kill switch가 우회되는 것과
동일한 결과를 낸다.

## 2. 모듈 분해 (최소단위)

### 2-A. 실행 소유권(Execution Ownership) — 신규 컨텍스트

| 파일 경로 | 신규/기존 | 단일 책임 | 공개 계약 | 의존 | 상한 | Zone |
|---|---|---|---|---|---|---|
| `src/foundation/execution_ownership/domain/models.py` | 신규 | 리스 값 객체 | `ExecutionLease(execution_id, owner_id, fencing_token, heartbeat_at, expires_at)` | 없음 | 60 | SCAFFOLD |
| `src/foundation/execution_ownership/domain/rules.py` | 신규 | 순수 판정 규칙 | `def is_lease_available(existing: ExecutionLease \| None, *, now, requesting_owner) -> bool` — 리스 없음, 만료, 또는 동일 소유자면 True | models | 60 | SCAFFOLD |
| `src/foundation/execution_ownership/ports/repository.py` | 신규 | 저장소 포트 | `class ExecutionLeaseRepository(Protocol)`: `async def acquire_or_renew_many(execution_ids, *, owner_id, ttl_seconds) -> set[int]`(획득/갱신 성공한 execution_id 집합만 반환), `async def release_all(owner_id) -> int` | models | 60 | SCAFFOLD |
| `src/foundation/execution_ownership/adapters/postgres_repository.py` | 신규 | asyncpg 구현 | 위 Protocol. `INSERT ... ON CONFLICT (execution_id) DO UPDATE ... WHERE` 조건부(§5) | conditional upsert | 150 | SCAFFOLD |
| `src/db/migrations/versions/<신규>_execution_leases.py` | 신규 | `execution_leases` 테이블(§3.1) | Alembic revision, `down_revision`은 착수 시 `alembic heads`로 확정(105번 §2-B 규칙) | — | 80 | SCAFFOLD |

### 2-B. 안전 게이트 배선 — 기존 파일 최소 수정 (신규 파일 없음)

| 파일 경로 | 수정 내용 | 근거 |
|---|---|---|
| `src/services/execution_loop/scheduler.py` | `ExecutionLoopScheduler.__init__`의 `pre_submit_gate: PreSubmitGate \| None = None` → `pre_submit_gate: PreSubmitGate`(필수, 기본값 제거). `distrust_monitor: DataDistrustMonitor \| None = None` 파라미터 신설(필수, 기본값 없음) 후 `_tick_one`의 `run_execution_tick(...)` 호출에 `distrust_monitor=self._distrust_monitor` 추가. `list_runnable()`을 `list_candidates()`로 이름 바꾸고, 새 `ExecutionLeaseRepository.acquire_or_renew_many(...)`로 필터링한 결과만 tick 대상으로 반환(§4) | I-01, I-02 |
| `src/services/background_loops.py` | `ExecutionLoopScheduler(...)` 생성 호출에 `pre_submit_gate=make_foundation_pre_submit_gate(pool)`, `distrust_monitor=DataDistrustMonitor(...)`, `lease_repo=PostgresExecutionLeaseRepository(pool)` 전달 | I-01, I-02 |
| `src/api/execution_deps.py` | `get_execution_service()`가 `ExecutionService`에 동등한 pre-start 게이트를 주입하는지 **재확인 필요**(§10 — `ExecutionService.__init__` 시그니처를 이 문서 작성 시점에 확인하지 못했다. 착수 전 재확인) | I-01 |

**의도적으로 하지 않는 것**: `src/services/order_service/gate.py`(`PreSubmitGate` 타입 정의)와
`foundation_gate.py`(구현체) 자체는 이미 완성돼 있으므로 수정하지 않는다 — 이 리프는 순수
배선(조립부) 문제다.

## 3. 계약 (Contract)

### 3.1 `execution_leases` 테이블

```sql
-- src/db/migrations/versions/<신규>_execution_leases.py
CREATE TABLE execution_leases (
    execution_id BIGINT PRIMARY KEY REFERENCES strategy_executions(id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,               -- 프로세스 식별자, 예: f"{hostname}:{pid}:{uuid4()}"
    fencing_token BIGINT NOT NULL DEFAULT 0,
    heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX ix_execution_leases_expires_at ON execution_leases (expires_at);
```

`fencing_token`은 QuantDinger `qd_strategy_runtime_leases`(`research_evidence_2026-09-03/
ext_quantdinger.md` §8) 패턴을 그대로 채택 — **소유자가 바뀔 때만** 증가한다. 이 문서는
fencing_token을 아직 소비하는 곳을 만들지 않는다(§10) — 리스 획득/거부만 이번 범위.

### 3.2 `ExecutionLeaseRepository.acquire_or_renew_many`

```python
# src/foundation/execution_ownership/ports/repository.py
class ExecutionLeaseRepository(Protocol):
    async def acquire_or_renew_many(
        self, execution_ids: list[int], *, owner_id: str, ttl_seconds: float
    ) -> set[int]:
        """execution_ids 중 리스를 획득했거나 이미 보유 중(갱신 성공)인 것만 반환한다.
        다른 owner_id가 만료 전 리스를 쥐고 있으면 그 execution_id는 반환하지 않는다."""
```

### 3.3 `ExecutionLoopScheduler` 시그니처 변경 (Before/After)

```python
# Before (현재)
def __init__(self, pool, *, resolve_adapter, policy, publish=None,
             max_concurrent_ticks=4, equity_tracker=None,
             pre_submit_gate: PreSubmitGate | None = None) -> None: ...

# After (제안)
def __init__(self, pool, *, resolve_adapter, policy,
             pre_submit_gate: PreSubmitGate,               # 기본값 제거 — I-01
             distrust_monitor: DataDistrustMonitor,          # 기본값 제거 — I-01
             lease_repo: ExecutionLeaseRepository,            # 신규 필수 인자
             owner_id: str,                                   # 이 프로세스의 리스 소유자 식별자
             publish=None, max_concurrent_ticks=4,
             equity_tracker=None, lease_ttl_seconds: float | None = None) -> None: ...
```

`pre_submit_gate`/`distrust_monitor`를 필수 인자로 바꾸는 것 자체가 I-01의 실행이다 — 앞으로
누구든 이 클래스를 생성하려면 컴파일/타입체크 시점에 게이트를 빠뜨릴 수 없다. 테스트 코드가
가짜 게이트(`async def _allow_all(ctx): return GateDecision(GateOutcome.ALLOW)`)를 명시적으로
넘기게 되므로, "테스트에서 게이트를 깜빡해서 기본 허용되는" 실수도 같은 원리로 차단된다.

## 4. 불변조건·상태기계

### 4.1 실행 소유권 불변조건 (I-02 적용)

- 어떤 `execution_id`도 유효한(만료되지 않은) 리스를 가진 프로세스 하나에서만 동시에 tick된다.
- 리스 갱신에 실패한 execution_id는 **이번 주기에 처리하지 않는다** — 재시도하거나 예외를
  던지지 않는다. 다음 주기에 다시 시도한다(리스가 만료됐다면 그때 획득 성공).
- `fencing_token`은 소유자가 바뀔 때만 증가한다. 같은 소유자의 반복 갱신은 증가시키지 않는다
  (QuantDinger 패턴 그대로 — 불필요한 토큰 소모로 하위 소비자의 "변경 감지"를 무디게 만들지
  않기 위함, 이 리프에서는 아직 하위 소비자가 없다).

### 4.2 게이트 배선 불변조건 (I-01 적용)

- `ExecutionLoopScheduler`/`ExecutionService`(재확인 필요, §10)는 `pre_submit_gate`가 주어지지
  않으면 **생성 자체가 불가능**하다(타입 시그니처 레벨 강제, 런타임 `if gate is None: raise`가
  아니라 애초에 Optional이 아니다).
- 게이트가 DENY를 반환하면 그 실행의 이번 틱은 신규 주문을 제출하지 않는다. 기존 포지션
  관리(청산 등)를 이번 틱에서도 계속할지는 **이 문서 범위 밖**(§10) — `foundation_gate.py`의
  기존 DENY 의미론을 그대로 물려받는다.

## 5. 동시성·멱등성·트랜잭션 경계 (105번 표준)

### 5.1 리스 획득 SQL (원자적, 조건부)

```sql
INSERT INTO execution_leases (execution_id, owner_id, fencing_token, heartbeat_at, expires_at)
VALUES ($1, $2, 0, now(), now() + $3 * interval '1 second')
ON CONFLICT (execution_id) DO UPDATE SET
    owner_id = EXCLUDED.owner_id,
    heartbeat_at = now(),
    expires_at = EXCLUDED.expires_at,
    fencing_token = CASE
        WHEN execution_leases.owner_id = EXCLUDED.owner_id THEN execution_leases.fencing_token
        ELSE execution_leases.fencing_token + 1
    END
WHERE execution_leases.owner_id = EXCLUDED.owner_id
   OR execution_leases.expires_at < now()
RETURNING execution_id;
```

`WHERE` 절이 거짓이면 `ON CONFLICT DO UPDATE`가 스킵되고(Postgres 표준 동작) `RETURNING`이
아무 행도 내지 않는다 — 이게 곧 "다른 소유자가 만료 전 리스를 쥐고 있어 획득 실패"의 신호다.
별도 조건부 UPDATE 헬퍼(`core/db/conditional_write.py`)를 재사용하지 않는 이유: 그 헬퍼는
"기존 행의 특정 컬럼이 기대값과 같을 때만 UPDATE"하는 단일 행 패턴이고, 이건 INSERT-or-UPDATE
+ 배치(여러 execution_id)를 한 왕복에 처리해야 해서 별도 SQL이 더 적합하다(구현 시 `executemany`
또는 `UNNEST($1::bigint[])` 배치 확인 — §10).

### 5.2 TTL 기본값 (Draft, 확정 아님)

`lease_ttl_seconds` 기본값 제안: `execution_loop.interval_sec × 5`(현재 1초 주기 기준 5초) —
QuantDinger가 "리스 기간의 1/3마다 갱신"하는 것과 유사하게, 매 tick 주기마다 갱신을 시도하면
5회 연속 실패(=5주기 동안 이 프로세스가 완전히 죽어 있었음)해야 다른 프로세스가 가져간다.
수치는 Draft — 운영 관측 후 조정한다(§10).

## 6. 실패 모드와 복구

| 실패 | 감지 방법 | 즉시 조치 | 복구 절차 | 감사 기록 |
|---|---|---|---|---|
| 리스 획득 실패(다른 프로세스가 보유 중) | `acquire_or_renew_many` 반환 집합에 없음 | 이번 주기 해당 execution 건너뜀 | 다음 주기 재시도, 상대 리스 만료 시 자동 획득 | 로그(WARNING) — audit_log까지는 이 리프 범위 밖 |
| 이 프로세스가 리스를 갱신하지 못함(DB 장애 등) | 동일 | 동일(자기 자신도 "실패"로 취급 — 예외적으로 봐주지 않는다) | DB 복구 후 다음 주기 | 동일 |
| 프로세스 정상 종료(SIGTERM) | `BackgroundLoops.stop()` 호출 | `lease_repo.release_all(owner_id)` 호출(신규, §10 — `stop()`에 연결 필요) | 즉시 다른 프로세스가 획득 가능(만료를 기다리지 않음) | — |
| 게이트가 DENY | `foundation_gate.py` 기존 동작 | 신규 주문 미제출 | 운영자가 kill switch 해제 | 기존 `foundation_gate.py`가 이미 `audit_log.risk_gate.unmandated_submit` 등 기록 |

## 7. 성능·SLO·관측성 (108번)

- 리스 획득 쿼리는 매 tick 주기(기본 1초)마다 1회 왕복(배치)으로 끝나야 한다 — execution마다
  개별 왕복하지 않는다(§5.1의 배치 처리가 이걸 보장).
- 메트릭 제안(108번 형식): `aios.execution_ownership.lease.acquire_failed.count_total`,
  `aios.execution_ownership.lease.owner_changed.count_total`(fencing_token 증가 횟수).
- 로그 필드: 리스 실패 시 `execution_id`, `requesting_owner`, `current_owner`(가능하면).

## 8. 테스트 계획

- **단위(순수 규칙)**: `is_lease_available()` — 없음/만료/동일소유자/타인점유 4개 케이스.
- **통합(실DB)**: 두 개의 "가짜 프로세스"(서로 다른 owner_id)가 동시에 같은 execution_id에
  `acquire_or_renew_many`를 호출 → 정확히 하나만 성공. 만료 후 재시도 → 성공 + fencing_token
  증가.
- **적대적**: `tests/adversarial/execution_ownership/test_no_double_tick.py` — 스케줄러
  인스턴스 2개를 같은 DB에 띄우고 동일 execution을 동시에 tick 시도 → 정확히 1회만 실행 경로가
  실제로 주문을 시도했음을 증명(mock adapter 호출 횟수로 검증).
- **게이트 배선 증명(I-10)**: `tests/adversarial/order_service/test_kill_switch_blocks_execution_loop.py`
  — safety_control을 ACTIVE로 만든 뒤 스케줄러를 통해 tick → 신규 주문 제출이 시도조차
  되지 않았음을 증명. 이게 이 문서가 요구하는 "우회 불가능성의 증거"(§7.3, OSS Deep Dive v2)다.
- **정적 검사(I-01)**: `ExecutionLoopScheduler.__init__`/`ExecutionService.__init__`의 안전
  게이트 관련 파라미터에 `Optional`/기본값이 없음을 AST 또는 타입 검사로 CI에서 단언하는
  테스트 1개 추가(신규 패턴 — 기존 AIOS에 선례 없음, 이 리프가 최초 사례가 된다).

## 9. 리프 목록 (제안 — 착수 순서, PM 승인 전 배정 금지)

| 리프 ID | 파일 | 선행 | DoD | 크기 |
|---|---|---|---|---|
| EO-01 | `domain/{models,rules}.py` + 단위 테스트 | — | §8 단위 케이스 4종 통과 | 120 |
| EO-02 | 마이그레이션 + `ports/repository.py` + `adapters/postgres_repository.py` + 통합 테스트 | EO-01 | §8 통합 테스트(동시 획득 경쟁) 통과 | 250 |
| EO-03 | `scheduler.py` 시그니처 변경(`pre_submit_gate`/`distrust_monitor`/`lease_repo` 필수화) + `list_candidates()` 재작성 | EO-02 | 기존 스케줄러 테스트 전부 시그니처 변경 반영 후 통과 | 150 |
| EO-04 | `background_loops.py` 조립부 수정(게이트·모니터·리포지토리 주입) + `BackgroundLoops.stop()`에 `release_all` 연결 | EO-03 | 적대적 테스트(중복 tick 방지) 통과 | 100 |
| EO-05 | `execution_deps.py`/`ExecutionService` 동등 배선(시그니처 재확인 후) | EO-04 (병렬 가능, 조사 선행) | kill switch 적대적 테스트(§8) 통과 | 미정(§10) |
| EO-06 | 정적 검사(I-01 CI 게이트) | EO-03 | Optional 안전 게이트 파라미터 0건을 CI에서 단언 | 80 |

## 10. 미확정·리스크

- **`ExecutionService.__init__` 시그니처 미확인**: 이 문서 작성 시점에 `pre_start_gate`(또는
  동등 개념)가 이미 존재하는지, 아예 없는지 확인하지 못했다. EO-05 착수 전 재확인 필수.
- **`DataDistrustMonitor` 생성자 인자**: 이 문서는 존재 확인만 했고(`src/core/safety/
  data_distrust.py:60`) 실제 생성에 필요한 인자(예: 데이터 소스 목록, 히스테리시스 임계값)를
  조사하지 않았다. EO-04 착수 전 확인 필요.
- **배치 SQL 방식**: §5.1을 `execution_id`별로 루프 도는 `executemany`로 할지, `UNNEST` 배열
  파라미터로 한 번에 처리할지 확정하지 않았다. 후자가 왕복 수를 줄이므로 선호되나 asyncpg
  타입 바인딩 확인 필요.
- **TTL 수치(§5.2)**: Draft. 실제 운영 tick 실패율 관측 후 조정.
- **fencing_token 소비자**: 이번 범위는 "획득/거부"까지다. 리스를 잃은 뒤에도 이미 시작한
  거래소 API 호출이 진행 중이면 그 호출 자체를 fencing token으로 무효화하는 것은 P0-R1의
  더 강한 버전이며, `AIOS_Target_Architecture_Freeze_v0.1` §1 Durable Workflow & Ownership
  Plane의 후속 범위로 남긴다.
- **`foundation_gate.py`의 `AIOS_REQUIRE_MANDATE_FOR_SUBMIT=0` 기본값**: 이 문서는 이 플래그를
  건드리지 않는다 — mandate 미첨부 실행을 DENY로 바꾸는 것은 완전히 별도 결정(레지스트리
  C-03과 연결되며, 더 큰 논의가 필요).
- **레거시 `strategy_executions` vs `foundation/paper_control` 이원화(C-01)**: 이 문서의 리스는
  `strategy_executions.id`를 기준으로 하며, C-01이 언젠가 해소돼 `foundation/paper_control`이
  canonical이 되면 이 테이블도 마이그레이션 대상이 된다 — 지금은 레거시 실행 경로를 기준으로
  최소 위험을 닫는 것이 목적이다.
