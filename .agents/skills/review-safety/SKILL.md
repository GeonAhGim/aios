---
name: review-safety
description: 주문 제출/승인 경로, 리스크 게이트, kill switch, circuit breaker, 컴플라이언스 축 검토 체크리스트
paths:
  - "src/core/safety/**"
  - "src/core/risk/**"
  - "src/core/executor/**"
  - "src/services/order_service/**"
  - "src/watchdog_process.py"
  - "src/foundation/mandates/**"
  - "src/foundation/execution_ownership/**"
tier: [S]
axis: safety
---

# review-safety

## 적용 조건

변경된 파일이 위 `paths` 중 하나에 걸리거나, diff가 주문 제출/승인, 리스크 합성 판정,
kill switch, circuit breaker, mandate 게이트, watchdog 청산 경로 중 하나를 건드릴 때
적용한다(tier S — Guard·XREV 교차 리뷰 대상). ADR-2026-09-06-I D4, `docs/design/INVARIANTS.md`
I-01·I-02·I-07·I-09·I-10·I-11이 이 축의 1차 규범이다.

## 체크리스트

1. 주문 제출·승인 경로의 생성자·함수 시그니처가 안전 게이트 인자(`require_mandate`,
   `kill_switch_state`, `mandate_revision_id` 등)를 `Optional`/기본값 `None`으로 받지 않는다
   [근거: I-01] [검사: scripts/check_consistency.py::check_env_keys]
2. env var·feature flag로 안전 게이트(mandate 필수·kill switch·circuit breaker)를 끌 수 있는
   조립부가 없다 — 특정 배포 시점의 값으로 안전 검사가 조용히 우회되면 안 된다
   [근거: I-01, I-11] [검사: scripts/check_consistency.py::check_feature_flags]
3. 다중 프로세스가 공유하는 실행-소유권 상태(`execution_ownership`, watchdog lease)는
   fencing token을 갖고 소유자 변경 시에만 증가하며, 갱신 실패 시 로컬 프로세스가 즉시
   중지한다 [근거: I-02] [검사: 후보]
4. 주문 최종 ALLOW/DENY는 RiskEngine 합성점과 Compliance 번들 평가라는 **두 개의 독립
   권위**를 모두 통과해야 하고, 각각 `risk_decision_id`/`compliance_decision_id` 조회
   증거를 남긴다 — 한쪽만 통과하고 통과로 처리하는 분기가 없다
   [근거: I-09] [검사: scripts/check_compliance_gate.py]
5. `correlation_with()`류 미지 페어·미지 심볼 조회가 실패 시 0.0/무상관/안전값으로 치환하지
   않고 `missing_pairs`에 채워 DENY(fail-closed)한다 — 결손을 안전으로 읽지 않는다
   [근거: FULL_AUDIT_2026-09-02.md, RTF-01] [검사: 후보]
6. Circuit Breaker 판정에 쓰이는 `data_delay_sec` 등 관측 지표가 트래커 미주입 시 `0`(정상)이
   아니라 `None`(모름)으로 전파되고, `_exceeds_or_unknown()`류 판정기는 `None`을 항상 임계
   초과로 취급한다 [근거: RTF-02] [검사: 후보]
7. watchdog `decide()`가 실제 `diagnose()` 결과(`failure_domain`, `market_wide_correlated`)를
   호출 시점에 전달받는다 — 호출 순서상 `decide()`가 `diagnose()`보다 먼저 실행되어 인자가
   기본값으로 고정되는 배선 결함이 없다 [근거: RTF-03] [검사: tests/unit/core/safety/test_watchdog_decide.py(AST 스캐너)]
8. mandate 게이트가 실제 프로덕션 조립부(현재 3곳: `background_loops.py` pre_submit_gate,
   `execution_deps.py` pre_start_gate, `oms/application/wiring.py` pre_send_gate) 전부에서
   `require_mandate=True`다 — 신규 조립부를 추가했다면 회귀 방지 AST 스캐너
   `_TARGET_FILES`에도 추가했다 [근거: RTF-04] [검사: tests/adversarial/oms/test_mandate_gate_prod_wiring.py]
9. `watchdog_process._apply_decision`·`circuit_breaker._set_level` 등 안전 상태 쓰기가
   조건 없는 `UPDATE`가 아니라 조건부 UPDATE(105번 표준)로, 동시 갱신 시 단일 쓰기만
   승리한다 [근거: RTF-05, RTF-06] [검사: scripts/check_consistency.py::check_router_wiring]
10. `strategy_allocation`류 분모가 `available_balance`가 아니라 `total_equity`다 —
    가용 잔고를 분모로 쓰면 레버리지가 반복 재배분될수록 과다 산정된다
    [근거: RTF-07] [검사: 후보]
11. 금액·수량·임계값 비교에 `float`가 아니라 `Decimal`을 쓴다(안전 게이트 임계 비교의
    부동소수 오차는 오탐/누락으로 직결) [근거: 11_implementation_rules_v1.2.md]
    [검사: scripts/check_consistency.py::check_money_float]
12. 안전/정책 컴포넌트는 "구현됨"이 아니라 실제 프로덕션 조립부에 배선되고 우회 불가능함이
    적대적 통합 테스트 또는 정적 스캐너로 증명되어야 완료다 — 순수 판정기 단위 테스트만
    있고 조립부 배선 테스트가 없으면 미완료로 REJECT [근거: I-10]
    [검사: scripts/check_zone_manifest.py]
13. 확인이 필요한 승인 동작(kill switch 해제, mandate 승인)은 클라이언트가 보낸 플래그만으로
    실행되지 않고, 서버측 1회성 토큰이 미리보기와 실제 실행을 연결한다
    [근거: I-11] [검사: 후보]

## 반례

### 반례 1 — 안전 게이트 인자에 `Optional` 기본값 (I-01 위반)

```python
# BAD: require_mandate가 기본값 False라 호출부가 인자를 빠뜨리면 조용히 우회된다.
def build_pre_submit_gate(*, require_mandate: bool = False) -> Gate:
    ...

# GOOD: 기본값 없음 — 조립부가 명시적으로 결정하지 않으면 타입 에러로 즉시 드러난다.
def build_pre_submit_gate(*, require_mandate: bool) -> Gate:
    ...
```

### 반례 2 — 미지 상관관계를 0.0(무상관)으로 fail-open

```python
# BAD: 알 수 없는 페어를 "상관 없음"으로 읽어 상관위험 규칙을 암묵 통과시킨다.
def correlation_with(self, symbol_a: str, symbol_b: str) -> float:
    return self._table.get((symbol_a, symbol_b), 0.0)

# GOOD: 계산 불가 페어는 missing_pairs에 채우고, 정책이 missing_pairs 존재 시 무조건 DENY.
def correlated_exposure(self, symbol_a: str, symbol_b: str) -> Decimal | None:
    if (symbol_a, symbol_b) not in self._table:
        return None  # 호출부가 missing_pairs에 채워 fail-closed DENY
    return self._table[(symbol_a, symbol_b)]
```

## 이 축에서 났던 사고

- `correlation_with()`가 표에 없는 심볼 페어를 상관 0.0으로 반환해 상관위험 규칙이 암묵
  통과되던 결함을 수정 (git:900704b)
- Circuit Breaker `data_delay_sec`가 상수 0으로 고정돼 stale 데이터에서 절대 트립하지 않던
  결함에 `DataFreshnessTracker`를 배선 (git:a0652c9)
- `foundation_gate`가 env 플래그(`AIOS_REQUIRE_MANDATE_FOR_SUBMIT`)로 mandate 검사를 배포
  시점에 조용히 끌 수 있던 결함을 제거하고 필수 인자로 전환 (git:a2e2646)
- 프로덕션 조립부 3곳이 `require_mandate=False`로 남아 있던 잔여를 resolver 배선 후
  전부 `True`로 전환(H-1b) — "구현됨≠배선됨"의 실제 재발 사례 (git:3d83e8d0)
