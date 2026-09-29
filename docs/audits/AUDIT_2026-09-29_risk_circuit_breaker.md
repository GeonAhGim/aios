# 리스크·서킷브레이커 적대적 감사 — task-8882

날짜: 2026-09-30. 범위: `src/foundation/risk_gate/**`(application/evaluate_pre_submit·
evaluate_risk_gate·activate_safety_control·deactivate_safety_control·recovery_gate·
intraday_monitor·read_fence·replay_decision, domain/fence·models·rules), `src/core/safety/
circuit_breaker.py`, `src/services/safety/circuit_breaker_loop.py`,
`src/exchanges/common/circuit_breaker.py`. 접합면으로 `src/services/order_service/
foundation_gate.py`(실제 `submit_order`에 배선된 `PreSubmitGate` 조립부), `src/services/
execution_loop/tick_risk_phase.py`(R-32 PRE_TRADE), `src/core/risk/rules/safety_state.py`
(R-13)도 확인했다.

이 리프는 **발견을 만드는 리프**다 — 코드 변경 없음(재현 서술만, 신규 프로덕션 코드 없음).
선행 감사 순서(§B-2(a))상 주문 경로(task-7978, S 3건) → 원장·회계(task-8675, S 0건/M 1건/L
3건)에 이어 3번째로 리스크·서킷브레이커 도메인을 연다. 이번 도메인에서도 task-8675의 방법론
교훈(레이어 분리 설계를 결함으로 오분류하지 않기 위해 인접 모듈까지 직접 추적)을 그대로
적용했다 — §2 참고.

## 0. 요약

| # | 심각도 | 제목 |
|---|---|---|
| F1 | S | 서킷브레이커 HALTED/EMERGENCY가 **최종 PRE_SUBMIT 게이트**(`foundation_gate.py`, 실제 `submit_order` 배선점)에는 배선돼 있지 않다 — CB를 아는 `evaluate_pre_submit.py`는 호출자가 전무한 죽은 코드이고, CB 격상이 kill switch(`safety_control`) 행을 만들지도 않는다. 실제 방어는 한 단계 앞선 PRE_TRADE(R-32) tick 판정뿐이라, PRE_TRADE 통과~어댑터 호출 사이의 창에서 CB가 새로 격상돼도 그 주문은 막히지 않는다 |
| F2 | M | `recovery_gate.py`(application, on-demand RECOVERY 경로)의 cooldown 이력이 실측이 아니라 합성 baseline(0) 샘플이다 — "충분한 시간이 지났다"만 증명하고 "그 구간이 실제로 안전하게 관측됐다"는 증명하지 못한다(자체 문서화된 R-54 미착수 갭) |
| F3 | L | `evaluate_risk_gate.py`가 `SafetyScope.STRATEGY_DEPLOYMENT` 범위의 kill switch를 아직 조회하지 않는다(자체 문서화된 #2026-09-02-28) — DEPLOYMENT/PRE_INTENT 게이트에서만 영향, PRE_SUBMIT/PRE_TRADE의 개별 주문 차단에는 영향 없음(§1-A 확인) |
| F4 | L | `insert_safety_control`(postgres_repository.py)에 동일 (scope, scope_ref)의 중복 ACTIVE 행을 막는 유니크 제약이 없다 — 차단 판정 자체는 "ACTIVE 행 존재 여부"만 보므로 안전에는 영향 없으나, fence 토큰·감사흔적이 오염될 수 있다 |
| F5 | L | `intraday_monitor.py` 문서가 주장하는 "RECON_MISMATCH는 별도 경로로 심볼 단위 DENY로 라우팅된다"(spec §6 line 473)는 이번 감사에서 직접 확인하지 못했다(미검증) |

대조표(§1)는 kill switch(비-CB) 차단·CAS 동시성 보호·fenced_submit F0/F1/F2·mandate/compliance
2중 권위 등 대부분 O이지만, **CB 강제정지 하나만 최종 게이트 기준으로 X**다. "발견 0건" 조건은
성립하지 않는다. 심각도 분포는 **S 1건, M 1건, L 3건** — 주문 경로 도메인(S 3건)보다는 양호하지만
원장·회계 도메인(S 0건)보다는 못하다. F1은 선행 감사의 핵심 패턴("강제정지 트리거가 실제 제출
경로에 배선돼 있는가")이 이 도메인에서도 재현된 사례다(§2).

## 1. 계약 대조표

### 1-A. risk_gate 게이트 종류별 — 모듈 × 항목

| 항목 | evaluate_pre_submit(R-35) | foundation_gate(R-36/37, 실배선) | evaluate_risk_gate(DEPLOYMENT/PRE_INTENT) | tick_risk_phase(R-32 PRE_TRADE) |
|---|---|---|---|---|
| 실제 `submit_order`/tick 경로에 배선됨 | **X** — `evaluate_pre_submit(` 호출자가 코드베이스 전체에 정의부 자신뿐(grep 확인) | O — `make_foundation_pre_submit_gate`가 `PreSubmitGate` 실구현체, 3사이트에 조립 | O — `start_deployment.py` 등 배포 시작 시 | O — `tick.py`가 매 tick 호출 |
| kill switch(`safety_control`) 차단 | O(설계상, 미배선) | O — layer 1, `foundation_gate.py:112-149`, `Optional`/기본값 없이 무조건 확인 | O — `rules.py::evaluate_risk`, 활성 control 최우선 | O — `list_active_controls` 매 tick 재조회, `tick_risk_phase.py:101-103` |
| circuit_breaker_level 차단 | O(설계상, 미배선) | **X** — layer 1은 fence+active_controls만 확인(`foundation_gate.py` 자체 docstring 10줄, "CB/data-distrust/connection-freshness는 아직 위임하지 않는다") | O — `safety_state.py:29-30`(`restricted/halted/emergency` → DENY), `risk_inputs_assembler.py` 경유 | O — `exposure_snapshot.py:58`이 `system_safety_state`를 매 tick 실시간 조회 |
| fence stale 재확인(F0→F1) | N/A(호출 안 됨) | O — `is_stale`, `foundation_gate.py:116` | N/A | N/A(별도 R-36/37이 t7에서 수행) |
| WORM 기록 | O(설계상) | O — `record_decision`, 모든 분기 | O | O — `recorder.record`, DENY도 기록(`tick_risk_phase.py:114`) |

**결론**: kill switch(비-CB) 차단은 PRE_TRADE·PRE_SUBMIT·DEPLOYMENT 세 경로 모두 O로 일관
되게 배선돼 있다. **CB 레벨 차단은 PRE_TRADE와 DEPLOYMENT에는 O지만 PRE_SUBMIT(최종,
어댑터 호출 직전 게이트)에는 X**다 — F1.

### 1-B. CircuitBreakerService(core/safety) — 항목별

| 항목 | 판정 |
|---|---|
| HALTED/EMERGENCY 자동 하향 불가(정책 8.6-B) | O — `_AUTO_DOWNGRADABLE = {WARNING, RESTRICTED}`, `circuit_breaker.py:일치 확인` |
| 관측 안 된 지표(`None`)를 항상 초과로 취급(fail-closed) | O — `_exceeds_or_unknown()`, RTF-02 매칭 |
| 상태 쓰기가 조건부 UPDATE(105번 표준) | O — `_set_level()`의 `expected` 스냅샷 CAS, 0행 매치 시 `ConcurrencyConflictError` |
| 재가동 4조건(evidence_ref∧이력∧approval∧fresh_risk) 전부 필요 | O — `can_reactivate()`, `recovery_gate.py`(core) |
| CB 격상이 kill switch(`safety_control`) 행을 생성해 PRE_SUBMIT을 우회 없이 차단 | **X** — `circuit_breaker.py`에 `safety_control`/`insert_safety_control`/`cb:` 관련 코드 없음(grep 확인). `circuit_breaker_loop.py:114-121`의 `_deactivate_cb_provider_controls`(재가동 시 `reason LIKE 'cb:%'` 행 정리)만 존재하고, 그 행을 **만드는** 배선은 같은 파일 docstring이 "격상 시 만드는 배선(420행)은 별도 리프라 오늘은 행이 없을 수도 있다"고 자체 인정 — 이것이 F1의 근본 원인 |
| `system_safety_state` 단일 테이블 공유(드리프트 없음) | O — CB 서비스의 `get_state()`/`_set_level()`과 risk_gate의 `read_safety_state()`가 같은 `WHERE id=1` 행을 읽고 씀(`postgres_repository.py:235-252`) |

**결론**: CB 판정 로직·상태쓰기 안전성 자체는 전부 O다. X는 "그 판정을 실제 차단 행동으로
옮기는 배선"에서만 발생한다(§1-A와 동일 결론, F1).

## 2. 선례 대조

- **task-7978(주문 경로) F1과 동일한 결함 클래스가 재현됐다**: "강제정지 트리거가 실제
  제출 경로에 배선돼 있는가"라는 질문에 대해, 이번에도 판정 로직(`evaluate_pre_submit.py`,
  `can_reactivate()`)은 올바르게 구현돼 있지만 실제 프로덕션 조립부(`foundation_gate.py`)에는
  연결되지 않은 부분이 있다. 차이점: 주문 경로 F1은 이벤트 배선이 전혀 없어 자기치유 경로가
  없다는 것이었고, 이번 F1은 한 단계 앞선 PRE_TRADE(tick_risk_phase.py)가 CB를 이미 정확히
  차단하고 있어 **완전한 fail-open은 아니다** — TOCTOU 창(PRE_TRADE 통과~어댑터 호출 사이)
  으로 좁혀진, 더 약한 형태의 동일 클래스 결함이다.
- **task-8675(원장·회계)의 방법론 교훈을 그대로 적용**: `activate_safety_control.py`가
  "STRATEGY_DEPLOYMENT 미배선"·"활성화가 이미 RUNNING인 배포는 막지 않음"을 자체 docstring에
  적어둔 것과 `recovery_gate.py`(application)의 합성 cooldown 이력을 최초에는 모두 "숨겨진
  결함"으로 의심했으나, `recovery_gate.py`(core)·`activate_safety_control.py` 원문을 직접 읽어
  이들이 (a) 의도적으로 추적된 미착수 리프(§9 R-54/#2026-09-02-28)이거나 (b) 별개 시스템
  간의 의도된 책임 분리(#2026-09-02-30, 배포 시작 차단 vs 실행-레벨 kill switch)임을 확인하고
  F2/F3으로 하향·재분류했다 — task-8675의 F1(M) 정정과 동일한 패턴.
- **fenced_submit(R-37)의 F0/F1/F2 재확인 시퀀스는 kill switch 레이스에는 완전히 유효하지만
  CB 레이스에는 무력하다** — F1/F2 재확인이 검사하는 것은 `fence_pairs_for()`의 5쌍 토큰
  (kill switch 활성화가 올리는 것)뿐이고, CB 레벨은 그 토큰에 반영되지 않기 때문이다. 이는
  "백스톱이 이미 존재하니 안전하다"고 잘못 판단하지 않기 위해 직접 코드를 추적해 확인한
  사항이다(fenced_submit.py 재확인).
- mandate/compliance 2중 권위(CM-8/CM-A5), `require_mandate` 무기본값 필수 인자, 3개 프로덕션
  조립부 전부 `True`(H-1b) 등 review-safety 체크리스트 항목들은 이 도메인에서도 재확인했고
  전부 O — 회귀 없음.

## 3. 실패 주입

| 시나리오 | 판정 | 근거 |
|---|---|---|
| 리스크 한도 평가 중 예외/타임아웃 → fail-open 누수 | O(안전) | `safety_state.py`의 각 필드는 `None`이면 `missing()`(RiskOutcome 미상 → R-16 evaluator가 결손을 DENY로 처리하는 계약, I2), 예외를 삼켜 ALLOW로 대체하는 분기 없음 |
| CB 상태전이 중 동시 갱신 레이스 | O(안전) | `_set_level()` CAS, 기존 테스트 `test_concurrent_reactivation_checks_only_one_instance_wins_the_transition`(`tests/integration/risk/test_circuit_breaker_loop.py:387`) |
| 개별 강제정지 트리거(가격 급변/손실한도/수동)가 실제로 종단까지 발화하는가 | 수동(관리자 API 경유 kill switch)은 O, CB 자동 트리거(가격급변/API장애 등)는 **PRE_SUBMIT 기준 X(F1)**, PRE_TRADE 기준 O | `activate_safety_control.py` 경유 수동 트리거 → `foundation_gate.py` layer 1이 무조건 차단(O). CB 자동 트리거 → PRE_TRADE(O)만 차단, PRE_SUBMIT(X, F1) |
| 재현 시나리오(F1) | 재현 스크립트 설명(실행 안 함, 기존 테스트 없음) | 아래 참고 |

**F1 재현 시나리오 설명** (신규 테스트 없음 — 기존 테스트 스위트에 이 경로를 직접 검증하는
adversarial 테스트가 없음을 확인, 향후 리프가 `tests/adversarial/risk/
test_cb_halted_blocks_pre_submit.py` 같은 이름으로 추가할 것을 제안):
1. `CircuitBreakerService.evaluate()`로 레벨을 HALTED로 격상시킨다(`system_safety_state.
   circuit_breaker_level='halted'`).
2. `make_foundation_pre_submit_gate(...)`로 만든 실제 `PreSubmitGate`를 호출한다(fence
   staleness 없음, 활성 kill switch 없음, mandate/compliance 통과 조건은 충족).
3. 기대(정책): DENY(`RISK_CIRCUIT_BREAKER_HALTED` 유사 코드). 실제: **ALLOW** — layer 1은
   `active_controls`만 보고, CB 격상은 `safety_control` 행을 만들지 않으므로 `active_controls`가
   비어 있다.
4. 대조 확인: 같은 CB 상태에서 `tick_risk_phase.run_pre_trade_risk_phase(...)`를 호출하면
   `safety_state()` 규칙이 DENY를 반환하고 `is_actionable()`이 False가 되어 t5에서 `None`을
   반환(주문 미생성) — 이 경로는 정상 동작한다.

## 4. 동시성

| 메커니즘 | 판정 | 근거 |
|---|---|---|
| 동시 `activate_safety_control`/`deactivate_safety_control` 레이스 | O | `deactivate_safety_control`이 `WHERE id=$1 AND state='ACTIVE'` 조건부 UPDATE(105번 표준), 0행 매치 시 `ConcurrencyConflictError`(`postgres_repository.py`) |
| 동시 `evaluate_pre_submit` 호출의 한도 소진 이중 통과 | N/A | 실제 배선된 PRE_SUBMIT(`foundation_gate.py`)에는 수치 한도 판정이 없다(fence+kill switch+mandate만). 수치 한도(`RiskLimit`)는 `src/core/risk`/`portfolio`(FROZEN_PAPER_ONLY, 이번 태스크 `FROZEN_PAPER_ONLY 승인: 아니오`로 편집 불가·범위 밖)에 있고, 그 축의 동시성은 이 태스크 스콥이 아니다 |
| CB `_set_level` 상태전이 레이스 | O | CAS + 기존 테스트(§3 인용) |
| `circuit_breaker_loop.py`의 프로세스-로컬 `history`(`MetricsHistory` deque)가 다중 워커 배포에서 어긋날 위험 | O(안전) | (a) `run_periodic_loop`이 매 tick을 `_run_instrumented`로 감싸 `except Exception`으로 광범위 포착·로그·계속 진행하므로 패자 프로세스의 `ConcurrencyConflictError`가 루프를 죽이지 않음(`background_loops.py:112-116`). (b) 재시작한 프로세스의 `history`가 비어 있어도 `can_reactivate()`는 `len(metrics_history) < cooldown_sec`을 DENY로 처리(core `recovery_gate.py:90-91`)하므로 짧은 이력은 항상 fail-closed 방향으로만 작용 — 조기 재가동 위험 없음. (c) 최종 전이 자체는 CB의 CAS가 보호 |
| `insert_safety_control`의 fence 토큰 `ON CONFLICT ... current_token+1` | O(의도된 설계) | 동시 활성화 전부가 서로 다른 토큰으로 성공해야 하므로 105번 표준(조건부 UPDATE로 하나만 승리)이 아니라 단조증가 카운터를 의도적으로 채택 — `postgres_repository.py` docstring에 근거 명시 |
| 동일 (scope, scope_ref)에 중복 ACTIVE 행 생성 가능 | X(경미, F4) | `insert_safety_control`에 유니크 제약 없음 — 차단 판정(`active_controls` 비었는지)에는 영향 없으나 감사흔적 중복 |

## 5. 결론

이 도메인은 **판정 로직 자체(순수 함수·CAS 동시성 보호)는 전부 O**이고, kill switch(비-CB)
차단은 PRE_TRADE·PRE_SUBMIT·DEPLOYMENT 세 경로 모두 견고하게 배선돼 있다. 그러나 선행 두
감사가 반복 확인한 "판정은 맞는데 실제 제출 경로에는 배선되지 않았다" 패턴이 **CB 강제정지
→ 최종 PRE_SUBMIT 게이트** 접합면에서 다시 나타났다(F1, S) — 다만 한 단계 앞선 PRE_TRADE가
이미 대부분의 케이스를 막고 있어 순수 fail-open은 아니고 TOCTOU 창으로 좁혀진 형태다. 심각도
분포는 **S 1건, M 1건, L 3건**으로, 주문 경로(S 3건)보다는 양호하지만 원장·회계(S 0건)보다는
못하다. F1의 권장 대응은 (a) CB 격상 시 PROVIDER 범위 `safety_control`(reason `cb:...`) 행을
실제로 생성하는 배선을 추가하거나, (b) `foundation_gate.py` layer 1에 `read_safety_state()`
호출을 추가해 `circuit_breaker_level`을 직접 재확인하는 것 — 추정 리프 크기는 소(테스트
포함 1개 파일 수정 + adversarial 테스트 1개, 기존 `read_safety_state` 재사용 가능하므로 신규
스키마 불필요). F2(recovery_gate 합성 이력)는 R-54(실측 이력 저장 테이블)가 별도로 이미
추적 중이므로 이번 감사에서 새 리프를 요구하지 않는다. F3~F5는 추정 리프 크기 극소(문서화
또는 유니크 제약 추가 1줄 수준)이나 이번 리프의 범위(발견 전용)를 벗어나 다음 안정화 라운드로
넘긴다.
