# ADR-2026-09-06-E: 전면 재검토 결과와 정정 — "게이트는 있는데 조립선이 끊겨 있다"

## Status
Accepted (2026-09-06, Chief Architect). 사용자 지시: "지금까지 만든 문서·구현을 모두 검토해 리팩터링해야 할 것을 줄이거나 지금 반영할 것을 조사하라."

## 방법
세 갈래로 병렬 감사했다. (1) 명세 ↔ 코드 소급 영향, (2) 명세 12종 상호 모순·리프 그래프 무결성, (3) 구현 코드의 미배선·구조 부채.
모든 발견은 `file:line` 증거를 요구했고, 근거 없는 추정은 채택하지 않았다.

## 핵심 결론
**실패 양식은 2026-09-02 전수감사 이후 사라진 것이 아니라 한 겹 안쪽으로 이동했다.** 게이트는 이제 존재하고, 코드상 우회 불가하며,
적대적 테스트도 있다. 없는 것은 **`main.py`/`background_loops.py`/`executor.py`가 그 게이트에 의존성을 넘겨주는 마지막 조립선**이다.
P0 8건 중 4건이 "인자 하나를 안 넘김"이고, 1건은 그 형태를 잡으라고 만든 검사가 **바로 그 형태를 정상이라고 단언**하고 있었다.

## 지금 반영한 것 — P0 (task 1714~1719)
| ID | 결함 | 증거 |
|---|---|---|
| P0-A | **서킷브레이커가 기동 직후 영구 HALTED** — `DataFreshnessTracker`가 테스트에서만 생성돼 `data_delay=None` → `_exceeds_or_unknown`이 True → HALTED, 자동 강등 불가. 모든 주문이 `RISK_CIRCUIT_BREAKER_HALTED`로 거부된다 | `main.py:106,112-118` · `background_loops.py:149` · `circuit_breaker.py:57,77-82,97` (실행 검증) |
| P0-B | **운영 주문 경로가 리스크·컴플라이언스를 둘 다 우회** — Executor가 `pre_submit_gate`를 안 넘기고, `submit_order`·`is_submission_allowed`가 `None`을 통과로 처리하며, `require_mandate=False`가 세 조립 지점에 있다 | `executor.py:113-115` · `submit.py:101,125` · `pre_submit_check.py:26,43-44` · `execution_deps.py:28` · `background_loops.py:238` · `wiring.py:64` |
| P0-C | **I-01 정적 검사가 무력** — `xfail(strict=False)`이고, 검사 범위를 생성자로 좁혀 `is_submission_allowed(gate=None)`을 정상으로 단언한다 | `tests/unit/test_gate_params_required.py:101-106,223-230,236-246` |
| P0-D | **WORM 결정 경로가 운영에서 0회 실행** — `fenced_submit`·`evaluate_pre_submit`의 src 임포터 0, `GateDecision.decision_id`가 어디서도 채워지지 않아 `orders.risk_decision_id`를 쓰는 운영 경로가 없다 | `fenced_submit.py` · `evaluate_pre_submit.py` · `gate.py:57` · `foundation_gate.py:12-18` |
| P0-E | **RLS 무력** — `tenant_transaction()` 운영 호출 0건, `ENABLE`(FORCE 아님)이라 소유자 우회, 레거시 3테이블은 정책만 있고 RLS off | `tenant_scope.py:1` · `b3c7f19ad2e6:53,74-80` |
| P0-F | **멱등 4중 스코프(I-03) 미배선** — 강제 지점의 임포터 0, 금전 POST는 옛 2중 스코프 사용, `purge_expired` 미호출로 테이블 무한 증가 | `api/contracts/idempotency.py:68,109` · `marketplace.py:56,159` · `admin.py:172-176` · `core/idempotency.py:133` |

## 지금 반영한 것 — 명세 정정
1. **FA-3/FA-4의 테이블명이 틀렸다.** `positions`·`position_journal`·`journal_entries`·`journal_lines`는 존재하지 않는다.
   실제는 `pos_snapshot`·`pos_journal`·`ledger_journal_entry`·`ledger_posting_line`. 워커가 그대로 실행했으면 실패하거나 레거시 테이블을 건드렸다.
2. **엔티티 계층의 진짜 선행 4건을 신설(FA-0a~0d).** ADR-B가 놓친 비용이다.
   (a) `tenant_id`가 `users(user_id)`를 FK하는 테이블 19개 — 조직 테넌트가 생기는 순간 전부 깨진다.
   (b) `portfolio_mandate UNIQUE(tenant_id)`가 테넌트당 포트폴리오 1개를 강제한다.
   (c) `ledger_account.account_code` 문자열 문법과 (d) `pos_snapshot.position_key`에 계층이 이미 인코딩돼 있어,
   컬럼만 추가하면 계층의 진실이 둘로 갈라진다. **이 넷이 `fund_id` 추가보다 먼저다.**
3. **범위 축소 3건.** 부모-자식 주문은 이미 구현돼 있고(`orders.parent_order_id`·`algo_slicer.py`), 컴플라이언스는 신설이 아니라
   `foundation/mandates` 확장이며(`foundation_gate`가 이미 리스크+mandate를 함께 평가), 양시간축은 WORM 테이블이 아니라
   **투영 테이블에만** 필요하다. 이벤트 소싱의 실제 공백은 도메인 테이블이 아니라 **영속화하지 않는 이벤트 버스**다.
4. **명세 간 모순 정리.** 심볼 마스터 이중화(LA vs DC → DC가 계약, LA가 저장, `instrument_id`는 UUID로 통일),
   entitlement 포트 소유권(LA-24가 정의, DC-9는 구현만), point-in-time 4중 구현(FA-9가 원천, 나머지는 위임),
   재현 키 3종·백테스트 설정 2종 단일화, `L4-25`·`L04/L05/L09/L11` superseded 표기와 선행 재배선,
   `submit_order` 시그니처를 만지는 5개 리프의 필드를 OMS 명세에 한 번만 선언.
5. **INVARIANTS I-09 정정.** "mandate ∩ RiskEngine 단일 합성점"은 ADR-B D4와 모순이라 리뷰어가 CM-8을 반려하게 된다.
   "두 개의 독립 권위를 모두 통과"로 고쳤다. I-07의 강제 지점이던 미정의 `F-04`는 실제 리프 `L36-a`로 정의했다.
6. **PM 지침 통합.** 날짜별 7개 절이 서로 다른 우선순위를 주장하고 있었다. 하나의 권위 절 + 11단계 순서로 합치고,
   "기존 컨텍스트가 있으면 신설이 아니라 확장" 규칙을 명시했다.

## 남은 정합성 항목 (P2, 별도 리프로 처리)
- 리프 ID 위생: `L42` vs `L4-2` 충돌, `IdempotencyScope` 동명이의 2종, 네 개의 레거시 `I<n>` 불변조건 표 네임스페이스 충돌,
  실체 없는 선행 토큰(`FND-08`·`alerts`·`ADR-A`·마이그레이션 해시), 구현됐지만 §9에 행이 없는 `LA-22/23/23b`.
- 강제 리프가 없는 불변조건: I-04(아티팩트 불변 DB 강제), I-05(백테스트=라이브 패리티 검증 리프), A-2, CM-A2, EM-A4.
- 배정 순서 위반 7건(CM-8·EM-3이 `L4-09`보다 앞 등)은 통합된 PM 순서로 대부분 해소됐고, 남은 것은 다음 사이클에 반영한다.

## 원칙 추가
**"명세가 신설처럼 적혀 있어도, 저장소에 같은 개념이 있으면 확장으로 배정한다."** 이번 감사에서 컴플라이언스·이벤트 스토어·
심볼 마스터·point-in-time 네 곳이 모두 중복 신설 직전이었다. 이 규칙을 PM 지침 §C에 강제 규칙으로 넣었다.
