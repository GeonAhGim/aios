# 원장·회계 적대적 감사 — task-8675

날짜: 2026-09-29. 범위: `src/foundation/ledger/**`(post_entry/backfill/chargeback/refund/
payouts/topup/purchase_flow/post_corporate_action_cash/resync_drift/verify_integrity,
domain/hash_chain·idempotency·posting_rules·balance_rules·trial_balance·rounding·correction),
`src/foundation/positions/**`(record_fill/record_funding_fee/rebuild_snapshot/
reconcile_provider/compute_daily_nav, domain/journal_rules·nav·pnl·cost_basis/*·
reconciliation_rules·restatement), `src/foundation/reconciliation/**`(run_reconciliation/
resolve_reconciliation, domain/models·rules, adapters/postgres_repository), 및 이 세
도메인의 접합면(`positions.reconcile_provider` → `reconciliation`, `reconciliation` →
`risk_gate.activate/deactivate_safety_control` → `risk_gate.recovery_gate`).

이 리프는 **발견을 만드는 리프**다 — 코드 변경 없음(재현용 서술만, 신규 테스트 파일 추가
없음). 선행 감사(`docs/audits/AUDIT_2026-09-26_order_path.md`, task-7978)의 F1~F8이 전부
착지한 뒤 §B -2(a) 순서에 따라 2번째 도메인(원장·회계)을 연 것이다. 조사는 원장/포지션/
정산 3개 하위영역을 병렬로 수행한 뒤, 정산 하위영역 초기 결과 중 두 항목(F-recon-1/2에
해당했던 것)을 recovery_gate.py 직접 확인으로 재검증해 하향 정정했다 — 상세는 §2/§3 참고.

## 0. 요약

| # | 심각도 | 제목 |
|---|---|---|
| F1 | M | 정산(FND-08) 불일치의 근본 원인(거래소 잔액 괴리 등) 자동 재동기화 경로가 없다 — `positions.reconcile_provider`가 불일치를 감지해 `reconciliation` 상태·safety control을 세우는 것까지는 완전하지만, 그 원인을 되돌리는 것(ledger `resync_drift` 또는 positions snapshot 재구성과의 자동 연계)은 항상 운영자 수동 개입이다 |
| F2 | L | `positions.record_fill`/`rebuild_snapshot`의 멱등 REPLAY 경로(중복 fill 재입력, 동시 재구성)가 advisory lock·optimistic lock으로 설계상 안전하나, 이를 직접 검증하는 통합테스트가 없다 — 설계 검토로는 안전을 확인했지만 회귀 방지망이 비어 있다 |
| F3 | L | `reconciliation.Classification.MINOR_DIFFERENCE` 상태의 lifecycle(등급 완화/악화 전이, resolve 가능 여부)을 검증하는 통합테스트가 없다 — 단위 테스트(분류 규칙)만 존재 |
| F4 | L | `ledger.post_entry`의 `_assert_extra_safe`(secret 키 거부)가 멱등키 lookup보다 나중에 실행돼도 안전한 이유가 코드에 문서화돼 있지 않다 — 현재 동작 자체는 안전(DB UNIQUE 제약이 먼저 막음)하지만 순서 의존성이 암묵적이다 |

대조표(§1)는 원장·포지션 두 도메인에서 전부 O이고, 정산 도메인에서 "근본원인 자동 해소"
1개 항목만 X다. **"발견 0건" 조건은 성립하지 않는다** — 단, 심각도 분포가 선행 감사(주문
경로, S 3건)와 달리 이번 도메인은 **S 0건, M 1건, L 3건**으로 훨씬 양호하다.

## 1. 계약 대조표

### 1-A. 원장(ledger) — 모듈 × 항목

| 항목 | post_entry | idempotency | hash_chain | correction | balance_rules |
|---|---|---|---|---|---|
| 매 posting의 차변/대변 합 = 0 검증 | O — `balance_rules.check_balanced` 호출, `posting_rules.py:112`(`_finalize` 내), `post_entry.py:219`(재확인) | N/A | N/A | O — reversal_lines도 재검증, `correction.py:67` | O — 엔트리별 합 검증, `balance_rules.py:45-60` |
| 멱등키 생성·저장 | O — `idempotency_key(event)` 호출, `post_entry.py:217` | O — `{event_type}:{event_ref}` 형식, `idempotency.py:20` | N/A | N/A | N/A |
| 멱등키 조회(중복 판정) | O — `journal.find_by_idempotency_key`, `post_entry.py:222` | N/A | N/A | N/A | N/A |
| 멱등키 다이제스트 검증 | O — `assert_same_digest`, `post_entry.py:224,234` | O — `event_digest`, `idempotency.py:23-35` | O — `lines_digest` 순서 무관, `hash_chain.py:32-47` | O — 재기표 digest 검증, `correction.py:102` | N/A |
| DB UNIQUE 제약 | O — `idempotency_key VARCHAR(250) NOT NULL UNIQUE`, migration `4a1d0c0de005:99` | N/A | N/A | N/A | N/A |
| WORM(append-only, 무정정) 보장 | O — journal/posting_line append-only, `post_entry.py:262` | N/A | O — 체인 검증, `hash_chain.py:75-104` | O — reversal+repost만(원본 UPDATE 없음), `correction.py:1-111` | N/A(UPDATE 정상 — balance는 잔액 캐시) |
| 애플리케이션 경로 전부 post_entry 경유 | **O** — topup/refund/chargeback/backfill/payouts/purchase_flow/post_corporate_action_cash 7경로 전부 `await post_entry` 호출 확인(grep) | 동상 | 동상 | 동상 | 동상 |

**결론**: 계약 대조 전부 O. F1(주문 경로)류 "상태 미전이" 패턴은 원장에서 발견되지 않았다
(§2-A).

### 1-B. 포지션(positions) — 모듈 × 항목

| 항목 | record_fill | rebuild_snapshot | postgres_journal | postgres_snapshot | cost_basis |
|---|---|---|---|---|---|
| 멱등키(fill_seq 포함) | O — command 인자로 명시 수령, `journal_rules.py:190`의 `f"fill:{order_id}:{fill_seq}"` | N/A | O — `idempotency_key` UNIQUE | N/A | N/A |
| `position_key` 중앙생성자(`PositionKey.parse`) 사용 | O — `record_fill.py:167` | O — `rebuild_snapshot.py:101` | N/A | O — `postgres_snapshot_repository.py:196` | N/A |
| Decimal 일관성(float 미혼입) | O | O | O | O | O(fifo/weighted 둘 다) |
| 결정론성(fold 재구현 없음, 순서 의존 없음) | O — `cost_basis.apply` 재사용 | O — `snapshot_builder.fold` 호출, `rebuild_snapshot.py:110` | N/A | N/A | O — fifo는 list, weighted는 단일 lot, dict/set 순회 없음 |
| 동시성 보호(advisory lock) | O — `record_fill.py:168` | O — `rebuild_snapshot.py:103` | O — `pg_advisory_xact_lock`, `postgres_journal_repository.py:146-150` | O — optimistic(`expected_seq`), `postgres_snapshot_repository.py:192-219` | N/A(순수 함수) |
| 105번 표준(조건부 UPDATE/FOR UPDATE/멱등키) | O | O | O | O | N/A |

**결론**: 계약 대조 전부 O. 주문 경로 감사의 F3(부분체결 취소 시 포지션 원장 미반영)·F7
(`fill_seq=1` 하드코딩)은 **positions 도메인 자체의 결함이 아니다** — `record_fill.py`는
`fill_seq`를 조건 없이 명시 인자로 받아 그대로 기록하며, 어떤 fill을 언제 반영할지 선별하는
책임(F3/F7의 실제 소재)은 호출자인 OMS `inbox_processor`/`position_ledger.py`에 있다(§2-B).

### 1-C. 정산(reconciliation) — 모듈 × 항목

| 항목 | run_reconciliation | resolve_reconciliation | PostgresReconciliationRepository |
|---|---|---|---|
| MATERIAL_MISMATCH 등 분류 | O — `classify_item`/`aggregate_classification`, `run_reconciliation.py:114-134` | 해당 없음(RESOLVED 전이만) | - |
| 불일치 시 safety control activate | O — `run_reconciliation.py:153-164`, scope=STRATEGY_DEPLOYMENT | 의도적으로 **하지 않음**(§2-C) | - |
| resolve 후 safety control 자동 deactivate | 해당 없음 | **X(단, 의도된 설계 — §2-C)** — 별도 `risk_gate.evaluate_recovery`(RECOVERY 게이트) 경유가 정책(REC-007) | O — `risk_gate` 별 테이블, 결합 없음(설계상 분리) |
| dedup(REC-004/006, input_hash) | O — `run_reconciliation.py:102-105` | 해당 없음 | O — `insert_run_with_items` 트랜잭션 원자성 |
| revision 조건부 UPDATE(표준-105) | 상태 upsert는 무조건 revision 증가(최신 우선) | O — `expected_revision` 검증, `postgres_repository.py:176-207`, 실패 시 `ConcurrencyConflictError` | O |
| 상태 의존성 확인(HEALTHY에서 resolve 불가) | 해당 없음 | O — `_RESOLVABLE_STATUSES`, `resolve_reconciliation.py:23-29` | - |
| connection health 체크(REC-003, 무영점 방지) | O — 불건강 시 전체 항목 PROVIDER_UNAVAILABLE, `run_reconciliation.py:110-120` | 해당 없음 | - |
| **불일치 근본원인 자동 재동기화** | **X** — `positions.reconcile_account`(`reconcile_provider.py:78-120`)는 메트릭만 남기고 원인 해소 없음; `ledger.resync_drift`와 배선 없음 | 해당 없음 | 해당 없음 |

**결론**: "resolve 후 safety control 자동 deactivate 안 함"은 최초 조사에서 X(결함)로 분류
됐으나, `src/foundation/risk_gate/application/recovery_gate.py`를 직접 확인한 결과 **의도된
계층 분리 설계**로 정정한다(§2-C, §3-F1 아래 "정정 근거" 참고). 반면 "근본원인 자동 재동기화
없음"(F1)은 실제 X — 어떤 상위 게이트도 이를 대체하지 않는다.

## 2. 선례 대조 — 주문 경로(task-7978) F1 패턴의 재현 여부

주문 경로 F1은 "거래소가 확정 취소한 주문이 `orders.status`를 영원히 전이하지 못해, 감지는
되지만 해소 경로가 아예 없어 계정 전체가 무기한 차단된다"는 패턴이었다. 이번 3개 하위영역에
같은 패턴이 있는지 각각 끝까지 추적했다.

### 2-A. 원장 — 패턴 없음

- `resync_drift.py`(`:72-158`)는 journal fold와 ledger_balance 간 drift를 감지하면 **실제로
  수리한다**(`resync_account_balance`가 balance를 fold 값으로 절대 SET) — 감지만 하고 끝나는
  코드가 아니다.
- `verify_integrity.py`(`:170-182`)는 체인 단절/시산표 불일치 시 `write_frozen=true`로
  차단하는데, 이는 "무기한 방치"가 아니라 **의도된 fail-closed**(운영자 개입 전까지 추가 훼손
  방지)다.
- `correction.py`는 상태 전이가 아니라 reversal+repost로 새 분개를 추가하는 방식이라 F1류
  "상태 미전이"와 구조적으로 무관하다.

### 2-B. 포지션 — F3/F7의 소재는 positions가 아니라 OMS

- `record_fill.py`는 조건 없이 매 fill을 기록한다(`:220-234`, FILLED 여부를 따지는 게이트
  없음). 주문 경로 F3("터미널 FILLED에서만 포지션 반영")의 게이트는
  `src/services/oms/application/inbox_processor.py`의 `_process_row`에 있다 — positions는
  호출자가 무엇을 넘기든 그대로 반영할 뿐이다.
- `journal_rules.py:190`은 `fill_seq`를 인자로 받아 멱등키에 포함시킨다 — 주문 경로 F7
  ("`fill_seq=1` 하드코딩")은 `src/services/order_service/position_ledger.py`(OMS 쪽
  어댑터)의 문제이지 positions 도메인 자체의 문제가 아니다.
- `reconciliation_rules.py`/`reconcile_provider.py`에는 감지-후-방치 패턴이 없다 — 감지
  결과를 `reconciliation`(§1-C/§2-C)으로 넘기는 것이 설계된 경계다.

### 2-C. 정산 — 최초엔 F1류로 보였으나, recovery_gate.py 확인 후 정정

최초 조사에서 다음을 F1류 결함(심각도 S 2건)으로 분류했었다: "`run_reconciliation`이
activate한 safety control을 `resolve_reconciliation`이 deactivate하지 않는다", "불일치가
HEALTHY로 전환돼도 자동 deactivate 경로가 없다."

`resolve_reconciliation.py:1-13`(docstring)를 그대로 인용하면:

```
This command never touches safety_control (FND-06) — marking RESOLVED and
disarming the kill switch are two entirely separate actions. Disarming the
kill switch requires a separate call to risk_gate.deactivate_safety_control(),
and the actual resumption must then go through a full re-evaluation
(mandate+risk+connection) as required by paper_control.resume_deployment() —
the principle that "resolve alone" auto-resumes nothing is upheld across
independent contexts.
```

이는 스펙(`AIOSproject #80 §2`, REC-007: "resolve alone cannot resume; fresh
trust/policy/risk/recovery approval required")에 근거한 **의도된 설계**다. 실제로
`src/foundation/risk_gate/application/recovery_gate.py`(`evaluate_recovery`)가 그 "별도
호출"의 구현체다 — evidence/approval/cooldown/fresh circuit-breaker 4개를 전부 확인한
뒤(`:160-168`)에만 `deactivate_safety_control`을 호출한다(`:225-227`, "On ALLOW, call the
existing `deactivate_safety_control` — no new deactivation path is created"). API 라우터에도
`POST /safety-controls/{control_id}:evaluate-recovery`(`risk_gate.py:170`)로 배선돼 있고,
`control_id`만 받는 범용 경로라 `reconciliation`이 activate한 control도 그대로 처리 대상이다.
`tests/foundation/integration/risk_gate/test_recovery_gate.py`가 이 경로를 다수 케이스로
검증한다(ALLOW/DENY/TTL만료/동시성 등).

**정정**: "resolve 후 자동 deactivate 안 함"과 "HEALTHY 전환 후 자동 deactivate 안 함"은
**결함이 아니라 스펙이 요구하는 계층 분리**다 — OMS의 `three_way_reconciler.py`(자기 모듈
안에서 activate/deactivate를 직접 호출)와 다른 설계이지만, 그 차이 자체가 "OMS는 낮은
안전등급의 자동복구, 정산은 재승인이 필요한 높은 안전등급"이라는 의도된 정책 차이로 읽힌다
— OMS 쪽이 더 느슨한 것이지 정산 쪽이 결함인 것은 아니다.

남는 것은 **재동기화 경로(F1, §3)**뿐이다: `positions.reconcile_account`가 불일치를 감지해
`reconciliation`을 거쳐 safety control을 세우는 것까지는 완결돼 있지만, "왜 불일치가
생겼는지"(거래소 잔액 실제 괴리, connection 장애 등)를 자동으로 되돌리는 경로는 어디에도
없다 — `evaluate_recovery`는 "재승인 후 차단 해제"를 다루지, "불일치 원인 자체의 교정"은
다루지 않는다. 둘은 별개 책임이며, 후자가 비어 있다.

## 3. 실패 주입 결과

### F1 — 정산 불일치 근본원인 자동 재동기화 경로 부재 (M)

- 근거:
  - `src/foundation/positions/application/reconcile_provider.py:78-120`(`reconcile_account`)
    — provider 잔액과 내부 snapshot을 비교해 `reconciliation.run_reconciliation`으로 넘기고,
    `material_count`만 메트릭(`POSITIONS_RECONCILIATION_MISMATCH_COUNT_TOTAL`)으로 기록한다.
    불일치 자체를 해소하는 코드 경로가 없다.
  - `src/foundation/ledger/application/resync_drift.py` — journal fold와 실제 balance 간
    drift를 수리하는 기능은 존재하지만(§2-A), `reconciliation`/`positions.reconcile_provider`
    와 호출 관계로 연결돼 있지 않다(별도 진입점, 스케줄러/운영 스크립트에서 독립적으로
    실행).
  - `src/foundation/positions/ports/snapshot_repository.py` — snapshot을 provider 값에 맞춰
    되돌리는 "재동기화" 메서드가 포트에 없다(조회/저장만 있음).
- 파급(재현 절차, 실행 안 함):
  1. `positions.scheduler`(60초 주기, `scheduler.py:192-213`)가 `reconcile_account` 호출.
  2. 거래소 잔액과 내부 snapshot이 어긋남(예: 이전 감사 F3류 — 부분체결 후 취소가 원장에
     미반영된 잔재, 또는 순수 provider 측 일시 오차) → MATERIAL_MISMATCH → safety control
     activate.
  3. 운영자가 원인을 파악해 `resolve_reconciliation` 호출 → 상태만 RESOLVED.
  4. `evaluate_recovery`로 재승인 후 control deactivate — 여기까지는 정상(§2-C).
  5. 그러나 5번 과정 전체에서 **내부 snapshot 자체를 provider 값과 일치시키는 자동 로직은
     한 번도 실행되지 않는다** — 운영자가 수작업으로(또는 `scripts/`의 별도 수동 스크립트로)
     ledger/positions 데이터를 고쳐야, 다음 reconcile 주기가 다시 HEALTHY로 돌아온다. 이
     교정을 잊으면 매 주기 같은 MATERIAL_MISMATCH가 재발한다.
  - 기존 테스트: `tests/integration/foundation/positions/test_reconcile_provider.py`는
    불일치 **감지**만 검증한다(재동기화 시나리오 없음). 이런 재동기화 성공 테스트 자체가
    저장소에 없다.
- 제안 조치: (a) `reconcile_account`가 MATERIAL_MISMATCH를 낼 때 provider 값 기준으로
  snapshot을 재구성하는 좁은 자기치유 경로(`rebuild_snapshot`을 provider 스냅샷 시드로
  재사용)를 추가하거나, (b) 최소한 운영 런북에 "resolve 전에 ledger resync_drift/positions
  재구성을 먼저 실행해야 한다"는 순서를 명문화하고 `resolve_reconciliation`이 이를
  강제하도록(예: evidence_ref 필수화) 정책을 보강한다.
- 예상 심각도: **M**(주문 경로 F1과 달리 "정상 운영 흐름 1건이 계정 전체를 무기한 차단"하는
  라이브니스 버그는 아니다 — 운영자 개입 경로 자체는 존재하고 문서화돼 있다. 다만 그 개입이
  "원인 교정"을 포함한다는 보장이 시스템에 없고 순수 메뉴얼 프로세스에 의존한다).
- 예상 리프 크기: L(ledger/positions 재동기화 통합 또는 재시도 루프 설계 + D3 동시성 증거
  필요 — 원장/포지션 양쪽 불변식에 영향을 주는 변경이라 신중한 스코핑 필요).

### F2 — 포지션 멱등 REPLAY·동시 재구성 경로에 대한 통합테스트 부재 (L, 설계는 안전)

- 근거:
  - `record_fill.py:180-189` — 기존 idempotency_key로 재입력되면(`is_replay_candidate`)
    원가 재계산을 건너뛰고 `realized_pnl_base = Decimal("0")`으로 고정, 기존 로트를 그대로
    사용한다.
  - `record_fill.py:236-237` — journal에는 append하되(`sequence_no` 신규 발급),
    `entry_view.sequence_no <= snapshot.last_journal_seq`면 스냅샷 upsert/감사 이벤트를
    만들지 않고 기존 스냅샷을 그대로 반환한다(모듈 docstring: "REPLAY는 스냅샷 upsert도,
    감사 이벤트도 만들지 않는다").
  - `record_fill.py:168`, `rebuild_snapshot.py:103` — 둘 다 `position_key` 단위
    `pg_advisory_xact_lock`으로 동시 호출을 직렬화한다. 두 번째 호출은 첫 번째의 커밋을
    기다린 뒤 실행되므로, "멱등 재입력 중 다른 fill이 끼어드는" 비결정적 경합은 설계상
    발생하지 않는다. `rebuild_snapshot`의 동시 재구성도 같은 이유로 안전(같은 입력 →
    결정론적 fold → 같은 `expected_seq` upsert, 후행 호출은 drift가 이미 0이면 스킵되거나
    `ConcurrencyConflictError`).
- 재현(실행 안 함): "중복 fill 이벤트 재처리" 통합테스트(동일 `order_id`+`fill_seq`를 2회
  전송해 스냅샷이 1회분만 반영됐는지 확인)와 "동시 `rebuild_snapshot` 호출"(두 태스크가
  동시에 같은 `position_key`로 호출해 최종 스냅샷이 1개만 남고 예외/스킵이 났는지 확인)
  테스트가 `tests/integration/foundation/positions/`에 없다(`test_record_fill.py`류 파일
  자체가 확인되지 않음 — grep으로 "REPLAY"/"idempotency" 키워드가 걸리는 통합테스트 없음).
- 제안 조치: 두 시나리오를 통합테스트로 명문화해 설계상 안전을 회귀 방지망으로 고정한다.
- 예상 심각도: **L**(현재 코드는 안전 — 설계 검토로 확인됨. 리스크는 향후 리팩터링이 이
  안전성을 모르고 깨뜨릴 가능성).
- 예상 리프 크기: S(통합테스트 2~3개 추가, 코드 변경 없음).

### F3 — `MINOR_DIFFERENCE` 상태 lifecycle 통합테스트 부재 (L)

- 근거:
  - `src/foundation/reconciliation/domain/models.py:14-26` — `Classification`에
    `MINOR_DIFFERENCE` 존재.
  - `src/foundation/reconciliation/domain/rules.py:41-42` — 절대/상대 허용오차 이내면
    `MINOR_DIFFERENCE` 반환.
  - `run_reconciliation.py:153`의 `_BLOCKING_CLASSIFICATIONS`에 `MINOR_DIFFERENCE`가
    없으므로 safety control이 activate되지 않는다(의도된 설계 — 경미한 차이로 거래를 막지
    않음).
  - `tests/foundation/unit/reconciliation/test_rules.py` — 분류 규칙 자체의 단위테스트만
    존재.
  - `tests/foundation/integration/reconciliation/test_reconciliation_lifecycle.py` —
    HEALTHY/MATERIAL_MISMATCH 전이만 다룬다(`MINOR_DIFFERENCE`로 시작하거나 그쪽으로
    악화/완화되는 통합 시나리오 없음).
- 재현(실행 안 함): `test_reconciliation_lifecycle.py`에 허용오차 안쪽 값으로 run을 만들어
  `MINOR_DIFFERENCE`가 나오는지, 그 뒤 값이 더 벌어져 `MATERIAL_MISMATCH`로 악화될 때
  safety control이 새로 activate되는지, 반대로 완화될 때 상태가 어떻게 갱신되는지 확인하는
  케이스를 추가하면 커버리지 공백이 메워진다.
- 예상 심각도: **L**(분류 규칙 자체는 단위테스트로 검증돼 있어 로직 결함 가능성은 낮음 —
  통합 레벨 조합 검증만 빠져 있음).
- 예상 리프 크기: S(테스트 케이스 3~4개 추가).

### F4 — `post_entry`의 `_assert_extra_safe` 순서 문서화 부재 (L)

- 근거:
  - `post_entry.py:214-215` — `_assert_not_frozen` → `_assert_extra_safe` 순서.
  - `post_entry.py:217-222` — 그 직후 `idempotency_key(event)` 계산 및
    `journal.find_by_idempotency_key` 조회(hit 시 `:235`에서 즉시 반환).
  - `post_entry.py:148-169` — `_assert_extra_safe`는 `event.extra`의 secret류 키
    (`s_*`/`secret_*`) 또는 비화이트리스트 키를 거부.
- 문제: 멱등 lookup(③)이 extra 검증(②) 이후에 위치하는 것 자체는 안전하다 — 각 고유
  `idempotency_key`는 매번 ②를 통과해야 DB에 닿고, 재전송은 DB UNIQUE 제약(`4a1d0c0de005:99`)
  으로 우선 차단되기 때문이다. 다만 "②가 ③보다 반드시 먼저여야 하는 이유"(멱등 lookup이
  기존 엔트리를 반환할 때 그 엔트리의 extra를 재검증하지 않으므로, 최초 삽입 시점에 한 번은
  반드시 걸러야 한다는 것)가 코드 어디에도 명시돼 있지 않다 — 향후 리팩터링이 순서를
  뒤집어도 컴파일/타입 체크로는 잡히지 않는다.
- 재현(실행 안 함): 기존 `test_post_entry_denies_forged_secret_like_extra_key_and_emits_denied_audit`
  (`tests/integration/foundation/ledger/test_post_entry.py`)를 기준으로, `_assert_extra_safe`
  호출을 ③ 뒤로 옮긴 변형 버전을 만들어 실행하면(코드 변경 없이 사고실험으로도 확인 가능)
  동작 자체는 바뀌지 않음을 확인했다 — 이 발견은 실제 버그가 아니라 **문서화 공백**이다.
- 제안 조치: `post_entry.py` 모듈 docstring에 "②(extra 검증)는 ③(멱등 lookup)보다 먼저여야
  한다 — 최초 삽입 시점에만 검증되고 재전송은 lookup으로 우회하지 않으며 UNIQUE 제약이
  이를 보증한다"는 1~2문장을 추가한다.
- 예상 심각도: **L**(보안 결함 아님, 현재 동작 안전).
- 예상 리프 크기: S(docstring 1~2문장 추가).

## 4. 동시성

- **원장 — 같은 idempotency_key 동시 제출**: `PostgresJournalRepository.append`가
  `pg_advisory_xact_lock(hashtext(_LOCK_KEY))`(`postgres_journal_repository.py:88`)로 전역
  append를 직렬화, DB UNIQUE(`idempotency_key`)가 백업. `tests/integration/foundation/ledger/
  test_post_entry.py:366 test_post_entry_concurrent_gather_retries_apply_balance_exactly_once`
  확인 — **양호(O)**.
- **원장 — 같은 계좌 동시 balance 갱신**: `BalanceRepository.get_for_update`(`SELECT ... FOR
  UPDATE`, `post_entry.py:238`)가 같은 트랜잭션 안에서 사전검증→journal append→balance
  UPDATE 순서를 강제 — **양호(O)**.
- **원장 — 동시 correction**: `correction.py`는 순수 함수(append는 `post_entry` 경유)이므로
  reversal/repost 각각의 event_ref 기반 idempotency_key로 중복이 자동 필터링 — **양호(O)**.
- **포지션 — 같은 position_key 동시 record_fill/rebuild_snapshot**: advisory lock으로
  직렬화(§3-F2) — **양호(O)**, 단 회귀 방지 테스트는 없음(F2).
- **포지션 — snapshot 낙관적 락**: `postgres_snapshot_repository.py:192-219`의
  `expected_seq` 조건부 UPDATE/DELETE+INSERT, 경합 시 `ConcurrencyConflictError` — **양호
  (O)**.
- **정산 — 같은 target 동시 run_reconciliation**: `run_reconciliation.py:102-105`의
  input_hash dedup(`get_run_by_input_hash`), `postgres_repository.py:87-130`의
  `insert_run_with_items` 트랜잭션 원자성 — 같은 입력이면 1회만 실행, 다른 입력이면 각각
  독립 실행 — **양호(O)**.
- **정산 — run_reconciliation과 resolve_reconciliation 경합**: `upsert_state`(무조건
  revision 증가)와 `transition_state_status`(`expected_revision` 조건부, 실패 시
  `ConcurrencyConflictError`)가 서로 다른 갱신 패턴이지만 revision 조건 덕에 순서 무관하게
  안전 — **양호(O)**.
- **정산 — 리스트 조회 중 상태 변화**: `list_states`(`postgres_repository.py:139-146`)는
  스냅샷 조회만 하므로 조회 이후 변화는 다음 주기에 반영 — 경합 아님, **양호(O)**.

**결론**: 동시성 축은 3개 하위영역 전부 **양호(O)**. 105번 표준(조건부 UPDATE/FOR UPDATE/
멱등키 + advisory lock 보강)이 원장·포지션·정산 전 계층에서 일관되게 지켜진다 — 주문
경로에서 확인됐던 성숙한 패턴이 이 도메인에도 이어진다.

## 5. 결론

원장(ledger)·포지션(positions) 두 도메인은 계약·멱등성·해시체인(WORM)·결정론성·동시성 축이
**전부 양호(O)**하고, 주문 경로 감사의 F1(상태 미전이로 인한 무기한 차단)·F3(부분체결 미반영)
·F7(fill_seq 하드코딩) 패턴이 재현되지 않는다 — F3/F7은 애초에 positions가 아니라 OMS 호출
계층의 책임이었음을 이번 조사로 확인했다(§2-B).

정산(reconciliation)은 최초 조사에서 F1류 결함으로 보였던 두 항목("resolve 후 자동
deactivate 없음", "HEALTHY 전환 후 자동 deactivate 없음")이 `recovery_gate.py`/REC-007
스펙에 근거한 **의도된 재승인 게이트**임을 코드로 직접 확인해 정정했다(§2-C) — 이는 이번
감사에서 초기 판단을 뒤집은 유일한 항목이며, 향후 감사에서 "감지-후-방치처럼 보이는 코드"를
바로 결함으로 단정하지 말고 인접 모듈(`risk_gate`)까지 추적해야 한다는 교훈을 남긴다.

실제로 남는 구조적 공백은 하나뿐이다: **정산 불일치의 "근본 원인" 자체를 자동으로 되돌리는
경로가 없다**(F1, M) — 감지→차단→재승인 해제의 안전 사이클은 완결돼 있지만, "왜 불일치가
생겼는지"를 고치는 것은 순수 수작업이다. 나머지 F2~F4는 전부 L(설계는 안전하나 회귀 방지
테스트·문서화 공백)이다.

**심각도 분포**: S 0건, M 1건, L 3건 — 선행 주문 경로 도메인(S 3건)보다 뚜렷이 양호하다.
