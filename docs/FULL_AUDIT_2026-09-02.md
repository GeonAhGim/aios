# mihwa-aios 전수 점검 보고서 (2026-09-02)

> 설계문서(00~17, ADR, L3 명세 71~81, 표준 103~108)부터 구현 코드·테스트·CI·git
> 이력까지, **프로젝트 목적 부합성 · 실체성 · 엔터프라이즈 방향성** 세 축으로
> 판정한 읽기 전용 감사 결과다. `RED_TEAM_FINDINGS.md`가 "재현 가능한 버그"를,
> `QUALITY_ELEVATION_ROADMAP.md`가 "청사진과의 간극"을 다룬다면, 이 문서는
> "지금 이 저장소가 무엇이고 무엇이 아닌가"를 한 번에 판정한다.
>
> - 기준 HEAD: `4e0f8db` (190 commits). 감사 중 다른 세션들이 계속 커밋해
>   보고서 저장 시점에는 `3f498b3`(FND-08 reconciliation, KIS 확장 등)까지
>   진행돼 있었다. 이 문서의 코드 인용은 `4e0f8db` 기준이며, 이후 커밋으로
>   바뀐 부분은 §1(게이트 재실행)과 §2-A(후속 조치)에만 반영했다.
> - 방식: 소스 384파일·테스트 156파일을 영역별로 전 파일 정독, 게이트(ruff/mypy/
>   pytest)를 CI와 동일 명령으로 로컬 Postgres 16 위에서 실행, 상위 결론 10건을
>   코드로 직접 재확인. 어떤 파일도 수정하지 않았다.

---

## 0. 판정

**스켈레톤이 아니다. 그러나 시스템도 아니다.** 28K 줄의 소스는 실제 SQL·조건부
갱신·실DB 테스트 1,105건으로 뒷받침되는 진짜 코드이며, `NotImplementedError`는
KIS 웹소켓 한 곳뿐이다. 하지만 전략→포트폴리오→리스크→실행 파이프라인을
주기적으로 호출하는 스케줄러가 운영 앱 어디에도 없고, 안전장치 대부분은
호출자가 없으며, 6.4K 줄의 foundation 계층은 실행 경로에서 단 한 번도
import되지 않는다. 현재 상태는 **"검증된 라이브러리 묶음"**이지 **"실행을
통제하는 트레이딩 OS"**가 아니다.

프로젝트 자신의 최상위 지침(103번: "기능의 양보다 P0 보완과 추적성이 우선")과
MVP 스콥(06번: Bitget 현물 5심볼, Market·Limit·TWAP)에 비추면, 지난 하루 동안
들어간 Bitget 확장 11개 그룹(Convert·Earn·Grid·P2P·Copy Trading 등, 제품 호출부
0개)과 06번이 명시적으로 제외한 Iceberg 구현은 **목적에서 벗어난 폭 확장**이다.
방향(헥사고날 foundation, 105번 동시성 표준, PAPER 하드 가드)은 맞다. 순서가
틀렸다.

| 질문 | 판정 | 요지 |
|---|---|---|
| 목적·의도 부합 | **부분 부합** | PAPER 전용 하드 가드, 결정론적 리스크 fail-closed, 멀티테넌트 스코핑, Zone 체계는 설계 철학 그대로. 반면 MVP 스콥 밖 거래소 API 폭 확장, FROZEN 존 안의 실제 엔진, 존재하지 않는 ADR 인용, P0 미해결 상태의 기능 확장은 프로젝트가 스스로 정한 우선순위와 충돌 |
| 형식상 기초만인가 | **아니다. 단, 배선 부재** | 코드 한 줄 한 줄은 실물. 문제는 "존재하되 연결되지 않은" 것이 핵심 경로에 집중돼 있다는 점. 실행 tick, Circuit Breaker 판정, Reconciliation, Data Distrust, 재시작 복구, 감사 체인, foundation 게이트 전부 호출자 0 |
| 엔터프라이즈 방향 | **방향은 맞고 순서는 틀림** | 아키텍처 선택은 적절. 그러나 관측성 0, 멱등성 부분, 트랜잭션 경계 누수, kill switch가 실행 중 배포를 멈추지 못함, 로그아웃 no-op, 키 회전 불가, 에러 봉투·페이지네이션 규약 미채택, 실제 돈이 새는 결함 4건. 이것들을 닫기 전에 넓히는 것은 공격 표면만 키운다 |

---

## 1. 실측 게이트 결과

CI 설정과 동일한 명령을 로컬 Postgres 16(docker) 위에서 직접 실행했다.

| 게이트 | 결과 | 비고 |
|---|---|---|
| ruff | 통과 | E·F·I·UP·B 규칙만 활성 |
| mypy --strict | 통과 | 387 파일 · `type: ignore` 178건 |
| pytest 1차 (HEAD `4e0f8db`) | **1,105 passed / 0 failed** | skip 0 · 13분 44초 |
| pytest 2차 (HEAD `8af24d7`) | 1,037 passed / 1 failed / **114 errors** | 15분 40초. ERROR는 전부 픽스처 셋업 단계, 서로 무관한 파일에 분산. 같은 파일 단독 재실행 시 전부 통과 → 다른 세션의 동시 DB 사용 간섭 |
| pytest 3차 (HEAD `1d1a1de`) | 1,153 passed / **7 failed** | 10분 05초. 실패 7건은 전부 `tests/foundation/*/reconciliation/` — 코드·테스트는 커밋됐는데 마이그레이션 `f2b8e5d1a734`가 공유 DB에 미적용이었음. 적용 후 통과 |
| 소스 / 테스트 | 28.2K / 23.5K 줄 | 테스트 함수 1,037 (1차 기준) |
| 커밋 | 190 | 8/27 → 9/2, 6일 · 8/28 하루 124개 |
| 커버리지 | 미측정 | pytest-cov 없음, 어떤 문서에도 수치 없음 |

mypy strict 통과의 실질: `type: ignore` 178건 중 160건이 거래소 믹스인의
`self._request`에 붙은 `[attr-defined]`다. Protocol 없이 믹스인을 쓴 결과 거래소
계층 3,200줄은 사실상 타입검사 밖에 있다.

2차·3차 실행의 불안정은 코드 결함이 아니라 §9에서 지적한 **테스트 격리 부재**의
실증이다. 여러 세션이 하나의 dev DB를 공유하면서 TRUNCATE·롤백 없이 uuid
접미사로만 격리하고, 마이그레이션 적용 시점이 세션마다 다르면 "N passed"
숫자는 실행할 때마다 달라진다. CI(세션당 새 Postgres)는 이 문제를 겪지 않으므로
로컬과 CI의 결과가 달라지는 구조다.

---

## 2. 돈·안전에 직결되는 결함 (P0)

전부 코드로 직접 재확인했다. "설계 부족"이 아니라 "지금 이 코드로 실행하면
일어나는 일"이다.

| 심각도 | 결함 | 근거 |
|---|---|---|
| P0 | **음수 가격 리스팅으로 지갑 증액.** 리스팅 가격에 `>= 0` 검증이 없고, 차감 SQL의 `balance >= amount`는 음수 amount에 항상 참. 판매자가 `price=-1000000`으로 등록하면 구매자(또는 자기 부계정) 잔액이 늘어난다 | `src/api/schemas/marketplace.py:19` (`Decimal \| None`, 제약 없음) · `src/services/wallet_service.py:100` |
| P0 | **구매 멱등키가 전역.** `purchase:{header}`만으로 키를 만들어 사용자 B가 A와 같은 헤더값을 보내면 A의 구매 응답(purchase_id, 정산액)을 받고 B의 구매는 일어나지 않는다. 402 실패 응답도 캐시돼 충전 후 재시도가 영원히 402 | `src/api/routers/marketplace.py:149-156` · `src/core/idempotency.py:28-43` |
| P0 | **동일 리스팅 재구매·환불 이중 적립.** `strategy_purchases`에 UNIQUE(listing, buyer)가 없고 서비스에도 중복 검사가 없다. RESOLVED 분쟁 뒤 새 분쟁을 열어 다시 환불받을 수 있다. 원장 테이블에는 WORM REVOKE가 없다 | `src/services/purchase_service.py:93-159` · `src/services/dispute_resolution_service.py:123-142` · 마이그레이션 `e5f6a7b8c9d0` |
| P0 | **검증담당자 이해상충 미강제.** 15번 §15.6이 "API 레벨 강제"로 못박은 유일한 RBAC 데이터 규칙인데, `seller_user_id`를 읽고도 `verifier_id`와 비교하지 않는다. 테스트도 없다 | `src/services/verification_service.py:71-80` |
| P0 | **PAPER 격리가 미검증 헤더 하나.** `paptrading: 1`이 스팟 엔드포인트에서 데모로 동작한다는 증거가 없고 docstring이 미확정을 자인한다. 이 헤더가 무시되면 Executor의 3중 가드를 통과한 PAPER 실행이 실계좌 주문을 낸다. 실계정 왕복 테스트는 한 번도 없었다(`tests/e2e/` 비어 있음) | `src/exchanges/bitget/adapter.py:8-10, 88-91, 155-165` |
| P0 | **손실한도 자동정지가 절대 발동하지 않는다.** `positions` 테이블에 쓰는 코드가 `src/`에 0건인데 RiskGuard·포트폴리오·리포트가 이를 JOIN한다. 운영에서 PnL은 항상 0. Watchdog의 `compute_equity`도 상수 0을 반환해 loss_pct 경로가 죽어 있다 | `src/services/risk_guard_service.py:49` · `src/watchdog_process.py:170-171` |
| P0 | **리스크 기준 상태가 인메모리.** 일손실·MDD 기준점이 프로세스 메모리에만 있어 재시작하면 "오늘 시작 equity = 지금 equity"로 리셋된다. 이미 발생한 손실이 사라지는 fail-open. 05번 §5.6 재시작 복구 절차는 구현돼 있으나 호출자가 없다 | `src/services/execution_loop/equity_tracker.py:3-6` · `src/core/event_bus/recovery.py` 호출 0건 |
| P0 | **Kill switch 직후 stale ALLOW.** policy 30초·risk 10초 캐시의 fingerprint가 revision과 safety control 상태를 포함하지 않아 pause·kill switch 직후에도 캐시된 ALLOW가 반환된다. kill switch는 start·resume만 막고 RUNNING 배포와 `submit_paper_intent`는 멈추지 못한다. fence token을 소비하는 워커가 없다 | `src/foundation/mandates/application/evaluate_policy.py:39-44` · `risk_gate/application/evaluate_risk_gate.py:68-75` · `paper_control/application/submit_paper_intent.py:42-55` |
| P1 | **RiskEngine이 레버리지를 검사하지 않으면서 검사했다고 기록.** `checked.append("leverage")`만 있고 비교가 없다. 감사추적에 "확인함"으로 남는다 | `src/core/risk/engine.py:88-90` |
| P1 | **risk_guard 루프에 try/except 없음.** 예외 1회로 루프가 영구 사망. 16번 §16.0-B가 "가장 심각"으로 지목한 패턴이 alert 루프에는 적용되고 여기엔 빠졌다. 레드팀 #25로 등록돼 있으나 미해결 | `src/main.py:131-135` |
| P1 | **체결가 영구 유실.** 주문 응답에서 `priceAvg`·`price`·`cTime`을 파싱하지 않아 `average_fill_price=None`이 그대로 DB에 영속화된다. 리컨실·PnL의 입력이 비어 있다 | `src/exchanges/bitget/trading_mixin.py:46-75` · `src/services/execution_loop/tick.py:129-135` |
| P1 | **승인요청 INSERT가 트랜잭션 밖.** rebalance 트랜잭션 안에서 다른 커넥션으로 승인요청을 커밋해 본 트랜잭션 실패 시 고아 승인요청이 남는다. 커넥션을 쥔 채 두 번째 커넥션을 획득하는 패턴은 풀 크기 10에서 교착 위험 | `src/services/portfolio_service.py:156, 226-239` |

### 2-A. 후속 조치 상태 (2026-09-02 기준, 커밋 `90feab5`)

| 결함 | 상태 | 조치 |
|---|---|---|
| 음수 가격 리스팅 | **수정됨** | 스키마 `Field(ge=0)` + `ListingService._validate_price` + DB CHECK. 테스트 3개 |
| 구매 멱등키 전역 스코프 + 실패 캐시 | **수정됨** | 키를 `purchase:{user_id}:{header}`로. `with_idempotency`를 claim-first로 재작성(2xx만 저장, 실패·예외는 선점 해제, 동시 요청 409, compute() 전 커넥션 반납). 테스트 2개 |
| 중복 구매·환불 이중 적립 | **수정됨** | `purchase()`가 FOR UPDATE 잠금 안에서 기존 구매 조회 + UNIQUE(listing_id, buyer_user_id). resolve는 `refunded_at` 조건부 UPDATE로 환불 1회. 테스트 2개 |
| 검증담당자 이해상충 | **수정됨** | `decide()`가 `seller_user_id == verifier_id` 거부. 테스트 1개 |
| PAPER 격리 미검증 헤더 | 미착수 | Bitget Demo 키 필요 — §11 4단계 |
| 실행 루프 미배선 (tick 호출자 0, §3) | **수정됨** (`2e943c9`) | `execution_loop/scheduler.py` — RUNNING·PAPER 실행을 `interval_sec` 주기로 tick, 실행별 실패 격리, LIVE 제외. `main.py` 백그라운드 태스크. 테스트 5개 |
| 재시작 복구 미배선 (§3) | **수정됨** (`2e943c9`) | `execution_loop/recovery_wiring.py` — 미결 주문 재조회·이벤트 재발행·audit_log. FILLED는 tick에 위임(PENDING 고착 방지). 테스트 3개 |
| Circuit Breaker `check_reactivation` 호출자 0 (§3) | **수정됨** (`2e943c9`) | `main.py` 10초 주기 루프. `evaluate(metrics)`는 지표 수집(positions·어댑터 계측) 이후 |
| `configure_logging` 미호출 (§3) | **수정됨** (`2e943c9`) | lifespan 첫 줄. trace_id·tenant_id 필드 관통은 §11 8단계로 남음 |
| risk_guard 루프 try/except 없음 (P1, 레드팀 #25) | **수정됨** (`2e943c9`) | — |
| 손실한도 자동정지 미발동 (positions 미기록) | 미착수 (9f 배정) | 체결→positions upsert. 스케줄러가 돌기 시작했으므로 이제 실제로 발동 가능한 경로가 됨 |
| 리스크 기준 인메모리 | 미착수 (9f 배정) | §11 6단계 |
| **신규** 취소·거부·만료 후 FSM이 PENDING에 고착 | 미착수 (9f 배정) | `cancel.py`·`tick._handle_pending_fill_check`·`recovery_wiring` 어디에도 BUY/SELL_ORDER_PENDING→이전 상태 복귀 로직 없음. 최종 상태가 FILLED가 아니면 실행이 영원히 새 신호를 평가하지 않는다 |
| Kill switch 직후 stale ALLOW — risk_gate | **수정됨** (`8a0734c`, 다른 세션) | `activate/deactivate_safety_control`이 `invalidate_evaluations`를 호출. PROVIDER 범위 조회 누락, scope_ref 없는 고아 통제도 같이 수정. 레드팀 #26~34 |
| Kill switch 직후 stale ALLOW — mandates | 미착수 (foundation 세션 배정) | `evaluate_policy._fingerprint`는 여전히 tenant+subject만 포함. mandate pause 후 30초간 캐시 ALLOW |
| Kill switch가 RUNNING 배포·intent 제출을 못 멈춤 | 미착수 (paper_control 세션 배정) | `submit_paper_intent` safety control 조회 + fence 소비 경로 |
| Bitget 확장 믹스인의 Executor 가드 우회 자금이동 (§4·§7, 레드팀 #32) | **수정됨** (`8a0734c`, 다른 세션) | `src/exchanges/common/live_guard.py`의 `@require_paper_sandbox`를 convert/grid/strategy/margin/futures/loan/subaccount에 적용. WS 재연결 시 로그인 서명 재사용(#31)도 수정 |
| P1 4건 | 미착수 | — |

### 2-B. 작업 배정 (PM: agent-platform-12, 2026-09-02)

여러 Claude 세션이 같은 clone·같은 dev DB를 동시에 쓴다. 배정은 각 세션이
이미 만진 파일 영역을 따르고, 아래 규칙으로 충돌을 막는다.

| 세션 | 담당 영역(기존) | 배정 작업 | 순서 |
|---|---|---|---|
| agent-platform-12 (PM) | 감사·marketplace | ① 실행 루프 운영 배선: `main.py` tick 스케줄러(`risk_policy.yaml` `interval_sec`), 실행 start가 태스크를 띄우도록, `recover_pending_orders` 호출, Circuit Breaker `evaluate/check_reactivation` 주기 호출 (§11 3단계) ② 전체 진행 추적·§2-A 갱신 | A |
| agent-platform-44 | foundation(trust/mandates/evidence/risk_gate/reconciliation), `foundation_deps`, `main.py` 라우터 등록 | ① mandates fingerprint(진행 중) ② `risk_guard` 루프 try/except(레드팀 #25, `main.py:131-135`) ③ foundation→실행 경로 연결: `order_service.submit` 앞 risk_gate PRE_SUBMIT, `execution_service.start` 앞 mandate 평가, 주요 커맨드 `append_audit_event` (§11 5단계) | A → B |
| agent-platform-c2 | connections, paper_control | ① `submit_paper_intent` safety control 조회(진행 예정) ② RUNNING 배포를 멈추는 fence 소비 경로 ③ paper_control 멱등키 digest(PAP-006)·REQUEST 중복 생성 차단·`ConcurrencyConflictError` 409 매핑 | A |
| agent-platform-9f | risk_gate, exchanges 가드, execution_loop/alert | ① `positions` 기록 경로(체결→포지션 upsert)로 RiskGuard·watchdog PnL 살리기 ② 일손실·MDD 기준점 DB 영속화(`equity_tracker`) + 재시작 시 복원 ③ RiskEngine 레버리지 실검사 또는 `checked`에서 제거, watchdog `compute_equity` 실계산 — FROZEN 존(`src/core/risk`) 수정은 PM 배정으로 승인됨 | A |
| agent-platform-30 | exchanges bitget/kis | ① `_request` HTTP 상태코드·429/5xx 백오프·`Retry-After`·서버시간 오프셋 ② WS ping/pong·ack 코드 검사·재연결 후 REST 재동기화 ③ `_row_to_order` priceAvg/price/cTime 파싱 ④ `get_positions` 실구현·심볼 역정규화 단일화 ⑤ (Demo 키 확보 후) `tests/e2e/` Bitget Demo 스팟 왕복 1회로 `paptrading` 헤더 실검증 | A → B |
| agent-platform-f0 | DevEngine, 레드팀 장부 대조 | ① `RED_TEAM_FINDINGS.md` 정합: 90feab5 4건 등재, CON-004 등재, #21 회귀 테스트, #05/#17 barrier 주입, #25 상태 ② 거버넌스(§11 9단계): `.aios-zone` 실제 경로·ADR-E 반영 + ADR-E 파일을 `docs/`에, CODEOWNERS 치환, CI에 zone 검증·pytest-cov 게이트·secret scan, 31초 sleep→클록 주입, 벤치마크 단언 추가·문서 덮어쓰기 제거 ③ 세션별 테스트 DB 분리 스크립트(`TEST_DATABASE_URL` per session) + README | A |
| agent-platform-6a | DevEngine 파이프라인 | DevEngine 유지. PR #2 정리 시 `src/core/utils/is_even.py`를 main에서도 제거 | — |

**공통 규칙**
1. 파일을 편집하기 전에 PM(agent-platform-12)에게 한 줄로 파일 경로를 공지한다. 다른 세션이 공지한 파일은 건드리지 않는다. `src/main.py`는 PM이 직렬화한다(편집 전 PM 승인).
2. `git add <자기 파일>`만. `git add -A`·stash·rebase 금지(같은 작업트리를 공유한다).
3. 커밋마다 즉시 `git push origin main`. 완료 보고는 커밋 해시 한 줄.
4. 마이그레이션을 추가하거나 pull했으면 `alembic upgrade head`를 공유 DB에 적용하고 PM에게 알린다.
5. PM에게 보내는 회신은 한 줄. 상세는 커밋 메시지와 `RED_TEAM_FINDINGS.md`에 쓴다.

마이그레이션 `b7e2c4d9f1a6`(revises `f2b8e5d1a734`)는 공유 dev DB에 적용됐다.
`wallet_transactions` WORM과 `strategy_listings` UNIQUE(strategy, version)은 이
커밋에 포함하지 않았다(각각 role 분리 결정과 재등록 정책 확인이 먼저 필요).

---

## 3. 실체는 있으나 배선이 없는 것

이 표가 "형식상 기초만인가"라는 질문의 정확한 답이다. 각 항목은 완성된 코드와
통과하는 테스트를 갖고 있다. 그리고 운영 앱(`src/main.py`)에서는 한 번도
실행되지 않는다.

| 구성요소 | 구현 상태 | src 내 호출자 | 결과 |
|---|---|---|---|
| 실행 루프 `run_execution_tick` | 완전 (FSM 조건부 전이, 멱등 주문, 체결 반영) | 0 | FD-8 전략→주문 파이프라인이 운영에서 dead. 실행 시작 API는 status만 바꾸고 태스크를 띄우지 않는다 |
| Circuit Breaker `evaluate / check_reactivation` | 완전 (halted·emergency 자동하향 금지 포함) | 0 | 레벨이 영원히 `normal` |
| Reconciliation, Data Distrust, 재시작 복구 | 완전 | 0 | 9.5·9.6·5.6 요구사항이 클래스로만 존재 |
| 주문 취소·정정·UNKNOWN 재조회 | 완전 | 0 | submit만 배선됨 |
| 구조화 로깅 `configure_logging` | 스키마 존재 | 0 | 운영 시 JSON Lines 출력 안 됨. trace_id·tenant_id 필드 전무 |
| 이벤트 버스 토픽 | 발행 측 완전 | 구독자 1 | `order.status.changed`, `risk.circuit_breaker.*`, `market.distrust.*`는 아무도 받지 않음. `audit.decision.logged`는 발행자 없음 |
| Foundation 7개 컨텍스트 (6.4K 줄) | Postgres 어댑터·마이그레이션·라우터·실DB 테스트 완비 | 0 (services·core·exchanges) | mandate·risk gate·kill switch·감사 체인 어느 것도 실제 주문 경로를 게이트하지 않는 병렬 섬. 프론트엔드도 foundation 엔드포인트를 하나도 호출하지 않는다 |
| 감사 이벤트 `append_audit_event` | 해시 체인·advisory lock·REVOKE 실구현 | 0 | foundation 내부 커맨드조차 감사 이벤트를 쓰지 않는다. 주문·리스크 판단은 `audit_log`에도 기록되지 않는다 |
| Bitget 확장 15개 믹스인 (~2,000 줄) | 서명된 HTTP 패스스루 | 0 | MVP 실행 루프가 실제로 밟는 어댑터 메서드는 5개. 확장 메서드는 Executor 가드를 거치지 않는 자금이동 경로(borrow, convert, transfer, grid) |
| `src/contracts/enterprise.py` 4개 계약 | frozen dataclass | 1 (호출자 없는 모듈) | foundation은 별도 `PolicyDecision`을 갖고 있어 계약이 이중화됨 |

---

## 4. 스콥·거버넌스 이탈

- **MVP 스콥 위반.** 06번 §6.4는 VWAP·Iceberg·SOR을 Phase 1 명시 제외로
  못박았다. 커밋 `0b26d34`는 Iceberg를 구현했다. 02b 스펙 자체가 §1.2에서
  "트레이딩 엔진 도메인 밖"으로 제외한 8개 그룹을 02c가 되살렸고, 하루 2.5시간
  동안 21커밋으로 들어갔다.
- **103번 절대원칙 위반.** "P0 미해결 영역에서 확장하지 않는다"(§6.1)가
  명시돼 있는데 P0-02(PAPER/LIVE 물리 격리), P0-03(멱등성·정산 원자성)이 열린
  채 확장이 진행됐다.
- **FROZEN 존 모순.** `.aios-zone`은 `src/core/strategy,portfolio,executor`를
  FROZEN으로 선언하고, 그 안에 647줄의 실제 판단 엔진이 있다. 근거로 인용된
  `ADR-2026-08-29-E`는 `C:\aios\mihwa-aios-docs\`에만 있고 저장소 `docs/`와
  `C:\aios\` 루트에는 없다. `src/core/risk/decision/**`로 지정된 FROZEN 경로는
  실제 파일(`risk/engine.py`)과 다르다. CI에는 zone 검증이 없고 CODEOWNERS의
  `@{owner}`는 치환되지 않았다.
- **DevEngine 스모크 아티팩트.** `src/core/utils/is_even.py`가 트레이딩 저장소
  코어에 남아 있다(PR #2).
- **리스팅 게이트 열림.** "3개월 Paper Trading 검증" 자격 검사가
  `_always_eligible`로 항상 True다(`src/api/marketplace_deps.py:22-26`). 거래소
  키 출금권한 검사도 경고 문자열만 반환한다.
- **테스트계획 미갱신.** 08번 §8.3-A는 지갑으로 대체된 `confirm_payment
  PENDING_PAYMENT`를 아직 요구한다. 10번 작업트리에는 상태 마커가 없어 "완료"를
  커밋 태그로만 추론해야 한다.

---

## 5. 영역별 상세 — 코어 엔진

`src/core`, `execution_loop`, `order_service`, `main.py`, `watchdog_process.py`.
03·05·06·07·09·11·48·105·108번 대조.

| 스펙 항목 | 상태 | 핵심 근거 |
|---|---|---|
| Loader / Parser / Validator / Scanner | 완전 | Pydantic 범위검증, 고아·자기순환·중복 검출 |
| StrategyEngine.evaluate | 부분 | `confidence=1.0`, `target_position=0`, `stop_loss=None` 상수(`engine.py:85-88`). 조건식은 순수 AND 또는 순수 OR만 |
| PortfolioEngine.allocate | 불일치 | 8.2-C 집계 없음, 반환형 변경. BUY=전액·SELL=전량 두 경우뿐 |
| RiskEngine 8지표 | 부분 | 7개는 fail-closed로 실구현. 레버리지 무검사. VaR 21캔들 정규근사, 상관관계 하드코딩 표. 48번 `RiskDecision`(5단계·rule_version·TTL·trace_id) 대비 3필드 스텁 |
| Executor PAPER 하드가드 | 완전 | LIVE 차단 + 어댑터 sandbox 이중검증(`executor.py:71-85`). LIVE는 "설계됨"이 아니라 "부재" |
| 주문 submit 멱등성 | 완전 | claim-then-send, DB UNIQUE, 커밋 후 발행. 단 `client_order_id`에 타임스탬프가 들어가 재시도마다 새 키 생성(`executor.py:87-89`) |
| Watchdog 별도 프로세스·heartbeat | 부분 | 원자적 파일 교체, split-brain 구현. equity 상수 0, `market_wide_correlated=None` 고정 → LIQUIDATE 절대 미발동 |
| 승인 워크플로 SOLO/DUAL | 완전 | 조건부 UPDATE+RETURNING으로 이중승인 방지. 만료는 lazy만 |
| Kill switch 5범위 (48번) | 부분 | GLOBAL과 실행 단위만. TENANT/ACCOUNT/PROVIDER 없음. `system_safety_state`는 id=1 단일 행이라 테넌트별 차단 불가 |
| 관측성 (07·108번) | 미구현 | trace_id 0건, 메트릭 라이브러리 없음, 리스크 거부가 `logger.info`로만 남음 |

---

## 6. 영역별 상세 — Foundation

71번 작업 패키지 FND-01~10 대 실제. 이 계층은 프로젝트에서 가장 설계 밀도가
높고 코드 품질도 가장 좋다. 그만큼 "섬"이라는 사실이 뼈아프다.

| WP | 상태 | 결손 |
|---|---|---|
| FND-01 trust | 부분 | membership·suitability 미구현, MFA step-up 없음(`mfa_verified = user.mfa_enabled`), consent 만료 항상 None |
| FND-02 mandates | 부분 | MAN-004 원자 활성화는 실구현. approval_binding 없음, 캐시 fingerprint 결함(P0 표), 감사 발행 0 |
| FND-03 evidence | 부분 | 체인·lock·REVOKE 실구현. 호출자 0. REVOKE는 소유 role에 무력한데 role 분리 증거 없음 |
| FND-04 strategy_packages | 축소 | `validation`으로 축소. 6개 체크 중 backtest 1개. `hard_fail_reasons` 항상 빈 튜플 → FAIL 판정이 구조적으로 불가능 |
| FND-05 connections | 부분 | 운영 DI가 `FakeReadonlyAccountProvider` 반환. "vault"는 AES로 감싼 fake ref. 스냅샷에 잔고·포지션 숫자 없음 |
| FND-06 risk_gate | 부분 | 5게이트 중 2개. fence 단조증가는 실구현, 소비자 없음. PROVIDER 범위 조회 누락(미커밋 diff가 수정 중) |
| FND-07 paper_control | 부분 | 상태머신·CHECK 제약 실구현. tick 스케줄러 없음, digest 없는 멱등키, 거부 결과가 재시도로 가려짐, 같은 키로 배포 중복 생성 |
| FND-08 reconciliation | 미커밋 WIP | 774줄 untracked, 미배선, 테스트 디렉터리에 `__init__.py`만 |
| FND-09 performance | 미구현 | 디렉터리 없음 |
| 공통 contracts/v1 · contract 테스트 | 미구현 | 106·107번이 요구한 `tests/foundation/contract/`, JSON Schema registry 부재. `SCHEMA_VERSION`은 검증되지 않는 기본값 문자열 |

응용 계층은 트랜잭션을 열지 않고 repo 호출마다 별도 커넥션을 잡는다.
accept_disclosure, begin/confirm_connection, start/request_deployment,
run_reconciliation 등 다단계 커맨드는 부분 실패 시 중간 상태가 남는다.
`src/foundation` 전체에 로깅·trace_id 사용이 0건이다.

---

## 7. 영역별 상세 — 거래소 어댑터

ABC와 Bitget 스팟 핵심 5경로는 02번 시그니처와 일치하고 HMAC 서명·Decimal
파싱은 올바르다. 운영 내구성은 없다.

- **HTTP 상태코드를 한 번도 읽지 않는다**(`adapter.py:112-127`). 429·5xx는
  JSON이면 Retryable, HTML이면 예외 계층 밖으로 새어 나간다. `Retry-After`·
  클라이언트 레이트리밋·서버시간 보정 없음. `get_server_time`은 구현돼 있으나
  미사용. 미지 오류코드가 일괄 Retryable이라 잔고 부족·최소수량 미달까지
  "재시도 가능"으로 분류된다.
- **WebSocket은 실소켓에서 지속 불가.** ping/pong 하트비트 없음(Bitget은 30초
  요구), subscribe/login ack 코드 미검사, 재연결마다 같은 타임스탬프·서명
  재전송(레드팀 #31 미해결), 재연결 후 REST 재동기화 없음.
- **`get_positions`는 Bitget·KIS 모두 항상 빈 리스트.** 심볼 역정규화가
  불일치해 주문 시 "BTC/USDT", 조회 시 "BTCUSDT"가 반환된다.
- **PAPER 시뮬레이터 부재.** 02번이 "최우선 구현 권장"한 슬리피지·부분체결·
  수수료 모사 어댑터는 착수조차 안 됐다. 백테스트용 `simulate_fill`은 실행
  루프와 미연결.
- **KIS.** 국내주식 일봉만. 토큰 발급 락 없음(분당 1회 제한 위반 가능), 만료
  토큰 무효화 없음, 잔고 연속조회 미처리(20종목 초과 절단), 가격 정정 불가.
  `market_hours`는 선언만 되고 읽는 코드 0.
- **테스트 183개 전부 MockTransport.** 실캡처 픽스처는 1개, 나머지는 "커뮤니티
  SDK 기준 최선 추정"이라 자인. 02c 그룹 테스트는 픽스처 에코 확인. 429·
  타임아웃·비JSON·토큰만료·WS 하트비트 테스트 0건.

---

## 8. 영역별 상세 — API·인증·지갑·DB

이 영역은 실물이다. Argon2id·TOTP 재사용 방지·잠금·타이밍 정규화, 지갑 원장의
조건부 UPDATE+단일 트랜잭션, 검증·분쟁의 낙관적 조건부 쓰기가 실제 SQL과
테스트로 뒷받침된다. Alembic 45개 리비전은 단일 head다. 그 위에서:

| 항목 | 상태 | 근거 |
|---|---|---|
| 엔드포인트 커버리지 (15·16번 56개) | 절반 이상 경로·메서드 편차 | `GET /marketplace/listings/{id}`, `/my-purchases` 미구현. 응답 봉투·`error_code`·페이지네이션 래퍼 어느 라우터도 미채택. 전역 exception_handler 0건 |
| ORM 계층 (16번 §16.0-A) | 부재 | `src/db/models/__init__.py` 0바이트, `target_metadata=None`. 스키마의 단일 진실은 손으로 쓴 SQL 마이그레이션 45개 |
| JWT | 최소 | `sub`+`exp`만. refresh·jti·revocation 없음 → **logout이 no-op**. 정지 계정은 매 요청 DB 조회로 차단(이 점은 좋음) |
| 잠금 카운터 | 비원자 | SELECT값+1 저장. 병렬 시도 시 카운트 손실. 423·`retry_after_seconds` 미준수 |
| Rate limiting | 전무 | grep 0건 |
| 테넌트 격리 | 양호 | 전 경로 `user_id` 조건. RLS 없음. IDOR급 결함은 멱등키 전역 문제 외 미발견 |
| 암호화 | 키 회전 불가 | AES-256-GCM 단일 키, 암호문에 키 버전 없음. base64→BYTEA 이중 인코딩 |
| 인증 이벤트 감사 | 미기록 | 로그인 성공·실패·잠금·MFA 변경·자격증명 등록·관리자 상태 변경이 `audit_log`에 남지 않음. `record_audit_log` 호출 5파일뿐 |
| audit_log WORM | 무력 | REVOKE는 있으나 앱이 소유자 role로 접속하면 효력 없음(마이그레이션 자인) |
| 인덱스·제약 | 누락 | listings UNIQUE(strategy, version), purchases UNIQUE(listing, buyer), 핫 쿼리 인덱스 4개 부재. `page=0` → OFFSET 음수 500 |
| Pydantic 제약 | 다수 부재 | rating 범위, topup amount > 0, price ≥ 0, platform enum, mode/currency 문자열 |

---

## 9. 검증 무결성 — 테스트·CI·git

**테스트 기반은 자체 보고보다 견고하다.** 1,037개 중 약 55%(566개)가 실제
Postgres를 때리고, skip·xfail·`assert True`가 0건이며, `TEST_DATABASE_URL`이
없으면 수집 단계에서 즉시 죽는다. 무작위 표본 45개 중 80%가 실제 도메인 상태를
단언한다. barrier 주입 경합 테스트(approval, execution control, evidence 10-way,
connections gather)는 이름값을 한다. 레드팀 22개 항목의 코드 수정은 22/22
확인됐다.

**그러나 "passed"가 유일한 지표다.**

- 커버리지 미측정. 벤치마크 테스트는 `len(samples)==N`만 단언하고 매 실행마다
  `docs/benchmarks/event_bus_latency.md`를 덮어쓴다(50ms 목표는 검증되지 않고
  기록만 됨).
- 거래소 테스트 158개(15%)는 "라이브 검증 필요"라 자인한 픽스처의 자기참조.
  적대적 테스트는 20개(2%)로 전부 cross-tenant 조회 거부만 검사. 변조·replay·
  fence 실경합은 DB 레벨 검증 없음.
- e2e 0건. 06번 §6.3 DoD의 "Bitget Demo 계정으로 주문 전송·취소·조회 성공"은
  한 번도 수행되지 않았다(6.11 태그 커밋도 MockTransport).
- 31초 실시간 sleep 3건(CI마다 93초). `MfaService`는 이미 `now=` 클록 주입을
  지원하는데 라우터 테스트가 쓰지 않았다.
- 레드팀 회귀 테스트 중 #05 ×2, #17은 barrier 없는 `gather`라 수정 전 코드로도
  통과 가능. #21은 테스트 없이 수정. CON-004는 장부에 항목 자체가 없다. #25는
  미해결.
- 테스트 격리는 TRUNCATE·롤백이 아니라 uuid 접미사. 공유 DB에 행이 누적되고,
  감사 도중 다른 세션의 pytest가 벤치마크 문서를 덮어쓰는 것을 직접 관찰했다.
  xdist 병렬화 불가 구조.
- CI에 zone 매니페스트 검증·의존성 allowlist·SAST·secret scan·커버리지 게이트
  없음(08번 §8.7 요구). ruff는 5개 규칙군만 활성이라 코드의
  `noqa: BLE001/S608/ARG002`는 실효 없는 장식.

**git 이력.** "리프 하나 = 커밋 하나 = 200줄 이내"라는 점진적 TDD 서사는 사후
구성이다. 190커밋 중 124개가 8/28 하루, 58개가 직전 커밋과 60초 미만 간격(다수
0초). foundation 커밋 7개는 1,188~2,400줄. 저자 184/190이 합성 신원
`AIOS Bootstrap`, 인간 PR 8건. 실질적 리뷰 게이트가 없다(branch protection은
요금제 제약으로 보류된 것이 문서에 정직하게 기록돼 있다).

---

## 10. 프론트엔드

`C:\aios\mihwa-aios-frontend`: React 19 + Vite 모노레포, 85파일 6.2K줄, 11커밋.
테스트 0건. 백엔드 엔드포인트 약 60개를 호출하지만 foundation 엔드포인트 참조는
0건이다. 즉 사용자가 실제로 만지는 화면과 foundation 안전 계층 사이에도 연결이
없다. 103번 P1-02가 요구한 page route→BFF→contract 매핑과 계약 테스트는
시작되지 않았다.

---

## 11. 권고 순서

103번의 착수 순서(§6.2)와 이 감사 결과를 합친 것이다. 원칙은 하나다.
**넓히지 말고 잇고, 이은 것을 증명하라.**

1. **확장 동결.** Bitget 02c 믹스인·FND-08 이후 신규 컨텍스트·프론트 신규
   화면을 P0 해소 전까지 멈춘다. 02c 믹스인은 제거하거나 별도 패키지로 격리해
   Executor 가드 밖 자금이동 경로를 닫는다(레드팀 #32).
2. **돈 결함 4건 즉시 수정 + 회귀 테스트.** price ≥ 0(스키마+서비스), 멱등키에
   user_id 스코프 + 실패 응답 미캐시, purchases UNIQUE(listing, buyer), 환불
   상태 컬럼, verifier ≠ seller. 반나절 분량이다.
3. **실행 루프를 운영 앱에 배선.** `main.py`에 tick 스케줄러
   (`risk_policy.yaml`의 `interval_sec` 사용), 실행 시작 API가 태스크를 실제로
   띄우도록, 재시작 복구 호출, Circuit Breaker evaluate 주기 호출, positions
   쓰기 경로. 이 단계 전까지 "PAPER 운영"은 성립하지 않는다.
4. **Bitget Demo 계정으로 e2e 1회 왕복.** 스팟 place/cancel/get 성공을
   `tests/e2e/`에 기록하고, `paptrading` 헤더가 스팟에서 유효한지 확정한다.
   불가하면 선물 Demo로 MVP 대상을 옮기든 PAPER 시뮬레이터를 만들든 결정해야
   한다. 이 하나가 P0-02의 절반이다.
5. **Foundation을 실행 경로에 연결.** `order_service.submit` 앞에 risk_gate
   PRE_SUBMIT, `execution_service.start` 앞에 mandate 평가, 주요 커맨드에
   `append_audit_event`. 캐시 fingerprint에 revision·safety control 상태 포함,
   `activate_safety_control`에서 `invalidate_evaluations` 호출, RUNNING 배포를
   멈추는 fence 소비 워커. legacy `system_safety_state`와 `safety_control` 중
   하나를 권위로 정한다.
6. **리스크 상태 영속화.** 일손실·MDD 기준점을 DB로, 레버리지 검사 실구현 또는
   `checked`에서 제거, watchdog equity 실계산.
7. **어댑터 내구성.** 상태코드 검사, 429/5xx 백오프, 서버시간 오프셋, WS
   ping/pong·ack 검사·재연결 시 재서명·재동기화, 응답 필드(priceAvg·cTime)
   파싱, 심볼 정규화 단일화.
8. **관측성 최소선.** `configure_logging` 호출, request_id 미들웨어, trace_id를
   주문·리스크·감사 경로에 관통, 리스크 거부를 `audit_log`에 기록, 인증 이벤트
   감사.
9. **거버넌스 정합.** `.aios-zone`을 실제 경로와 ADR-E 상태로 갱신하고 ADR-E
   파일을 저장소에 넣는다. CI에 zone 검증·pytest-cov 게이트·secret scan 추가,
   31초 sleep을 클록 주입으로 교체, 벤치마크에 단언과 문서 덮어쓰기 제거,
   `is_even.py` 삭제, CODEOWNERS 치환, 10번 작업트리에 상태 마커, 08번
   테스트계획 갱신, CON-004를 레드팀 장부에 등재.
10. **그 다음에** FND-08/09, 계약 테스트, 프론트-BFF 매핑, 02c 재검토.

---

## 12. 방법과 한계

- 설계문서 전량(00~17, ADR 6건, L3 명세 48·71~81, 표준 103~108, 저장소 docs
  2건)을 읽고, 소스 384파일·테스트 156파일을 영역별로 전 파일 정독했다. 다섯
  영역(코어·foundation·거래소·API/DB·검증무결성)을 병렬 감사한 뒤 상위 결론
  10건을 코드로 직접 재확인했다.
- ruff·mypy·pytest는 CI와 동일 명령으로 로컬 Postgres 16 위에서 실행했다.
  실거래소(Bitget Demo·KIS 모의투자) 호출은 키가 없어 수행하지 않았다.
- 감사 도중 다른 세션이 저장소를 수정하고 있었다. 기준 HEAD는 `4e0f8db`이며,
  미커밋 변경과 untracked reconciliation 모듈은 "진행 중"으로만 표기했다.
- docs 18~102(AIOSproject 저장소) 중 로컬에 없는 38~47, 49~70, 82~102는
  103번의 요약을 통해서만 참조했다.
- 이 감사는 어떤 파일도 수정하지 않았다(이 문서 추가 제외).
