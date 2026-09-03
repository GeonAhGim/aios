# AIOS Capability ↔ Open-Source Deep Dive v2 — 코드 레벨 교차검증 완료

작성: Fable | 2026-09-03 | 1차 Deep Dive(`docs/research/AIOS_Capability_Benchmark_DeepDive_v1_...md`)와
[검토 의견서](AIOS_Codex_Research_Review_by_Fable_v1_2026-09-03.md)의 후속. 원본 근거는
[`research_evidence_2026-09-03/`](research_evidence_2026-09-03/)에 7개 파일(내부 감사 3 + 외부 저장소 4)로
보존했다.

> **이 문서가 하는 일**: v1이 미룬 숙제 — "QuantDinger/LEAN/AgenticTrading/OBaI/Freqtrade를 코드 레벨까지
> 검증한다" — 를 끝내고, 그 결과를 AIOS 실제 코드 상태(직접 감사)와 3×5 매트릭스로 교차대조해 무엇을
> 얼마나 급하게 가져와야 하는지 우선순위를 매긴다. 표를 늘리는 문서가 아니라 표를 줄이는 문서다.

> **[2026-09-03 갱신] 이 문서 초안 작성 중 [연구 결정 기록](AIOS_Codex_Fable_CrossReview_Research_Direction_Record_2026-09-03.md)이
> 나왔다. 그 기록이 §5의 "Tier 0 — 지금 당장 코드 배선" 권고를 명시적으로 채택하지 않고
> ("Production Implementation: DEFERRED"), 같은 발견을 P0-R1/R2/R3 **연구·검증 항목**으로만
> 등록하기로 결정했다. §5는 초기 판단을 그대로 남겨 기록으로 보존하되, **§7에서 그 결정에 맞춰
> 결론을 갱신**했다 — §7이 최신 입장이다. 이 세션은 결정 기록의 원칙에 따라 `aios` 프로덕션
> 코드를 수정하지 않는다.

---

## 1. 검증 방법과 원본

| 대상 | 방법 | 결과물 |
|---|---|---|
| AIOS 런타임/실행 | 소스 직접 감사 (읽기전용) | [`aios_runtime_execution.md`](research_evidence_2026-09-03/aios_runtime_execution.md) |
| AIOS 인증/게이트웨이 | 소스 직접 감사 | [`aios_auth_gateway.md`](research_evidence_2026-09-03/aios_auth_gateway.md) |
| AIOS 전략 라이프사이클 | 소스 직접 감사 | [`aios_strategy_lifecycle.md`](research_evidence_2026-09-03/aios_strategy_lifecycle.md) |
| QuantDinger | 로컬 clone, 코드 레벨(500줄) | [`ext_quantdinger.md`](research_evidence_2026-09-03/ext_quantdinger.md) |
| LEAN | 로컬 clone(sparse), 코드 레벨(693줄) | [`ext_lean.md`](research_evidence_2026-09-03/ext_lean.md) |
| AgenticTrading + OBaI | 로컬 clone, 코드 레벨(242줄) | [`ext_agentictrading_obai.md`](research_evidence_2026-09-03/ext_agentictrading_obai.md) |
| Freqtrade | 로컬 clone, 코드 레벨(497줄) | [`ext_freqtrade.md`](research_evidence_2026-09-03/ext_freqtrade.md) |

모든 인용은 `파일:줄` 단위로 원본 문서에 있다. 이 문서는 결론만 압축한다.

---

## 2. 저장소 스냅샷

| 저장소 | 라이선스 | 규모 | 최근 커밋 | 핵심 정체성 |
|---|---|---|---|---|
| QuantDinger | Apache-2.0 | ~26K LOC(백엔드) | 2026-09-02 | Agent Gateway + 스코프 토큰 + Strategy API V2 |
| LEAN | Apache-2.0 | 대형(C#) | 2026-09-01 | 주문 상태기계 + 브로커리지 모델 번들(백테스트/라이브 패리티) |
| AgenticTrading | OpenMDW-1.0 (비표준) | ~278K LOC | 2026-09-02 | 에이전트 마켓플레이스 + 실 라이브(Robinhood MCP) |
| OBaI | Apache-2.0 + Commons Clause | ~115K LOC | 2026-08-21 | 구조화 Strategy JSON + MCP 마이크로서비스 |
| Freqtrade | **GPL-3.0** (코드 차용 불가, 패턴만) | 대형 | 2026-09-02 | 거래소 어댑터 내구성 + dry-run 안전장치 |

라이선스 주의: **Freqtrade는 GPL-3.0**이라 코드를 직접 옮기면 AIOS 전체가 카피레프트 의무를 지게 된다 —
아래 권고는 전부 "패턴만" 채택이고 QuantDinger/LEAN(Apache-2.0)은 상대적으로 자유롭다. AgenticTrading의
OpenMDW와 OBaI의 Commons Clause는 표준 OSI 라이선스가 아니므로 코드 재사용 전 법무 검토가 필요하다.

---

## 3. 핵심 발견 — 두 개의 놀라운 수렴

코드를 실제로 열어보기 전에는 몰랐던, 그리고 우선순위를 완전히 바꾸는 두 가지 발견이 있다.

### 3.1 "가져와야 할 것"의 상당수가 AIOS에 이미 죽은 코드로 존재한다

- `src/contracts/enterprise.py` + `src/services/paper_strategy_projection.py`에 **QuantDinger의
  Strategy API V2, OBaI의 StrategyDefinition과 거의 동형인** `StrategyPackage` 계약이 이미 있다 —
  `content_hash`, `risk_envelope`, `hypothesis`, `validation_refs`, `license_ref`까지. 그런데 **어떤
  API 라우터도, 어떤 DB 테이블도 이걸 참조하지 않는다.** 유닛테스트 1개가 유일한 호출자다
  (`aios_strategy_lifecycle.md` §1).
- `src/services/oms/**`에 LEAN의 `Order`/`OrderTicket`에 준하는 **11상태 전이표, outbox/inbox
  Protocol, idempotency 도메인**이 이미 완성돼 있다 — 그런데 실제 실행 경로(`order_service/*`,
  `tick.py`)는 이걸 전혀 호출하지 않는 병렬 섬이다(`aios_runtime_execution.md` §2).
- `src/foundation/paper_control/**`에 QuantDinger의 배포 상태기계(`REQUESTED→READY→RUNNING→...`)와
  거의 동일한 정교한 배포 모델(fence_token, mandate_revision_id)이 있는데, `package_ref`가
  불투명 문자열이라 `strategies` 테이블과 연결되지 않고, 실행 틱(`tick.py`)이 이 경로를 아예
  참조하지 않는다(`aios_strategy_lifecycle.md` §4).

**의미: "오픈소스에서 새 코드를 가져온다"보다 "이미 있는 코드를 배선한다"가 더 큰 지렛대다.** 세
경우 모두 새로 설계할 필요 없이 기존 계약을 실제 API/실행 경로에 연결하기만 하면 된다.

### 3.2 AIOS의 최악의 버그와 OBaI의 설계 결함이 구조적으로 동일하다

`aios_strategy_lifecycle.md` §3이 찾은 것: AIOS 검증 파이프라인의 `hard_fail_reasons`가
**구조적으로 항상 빈 튜플**이라 FAIL 판정이 코드로 불가능하다(`start_validation.py:180`).

`ext_agentictrading_obai.md` Part B §3이 찾은 것: OBaI의 전략 채택 게이트(`consistency_score<60%`,
`degradation>0.5`)가 **시스템 프롬프트 문자열로만** 존재하고, 최종 accept/reject는 LLM 자신이
내린다 — 코드가 강제하는 게 아니다.

두 시스템 모두 "게이트가 있는 것처럼 보이지만 실제로는 아무것도 막지 않는다"는 같은 실패 양식이다.
독립적으로 발견된 이 수렴은 우연이 아니라 **"검증 로직을 도메인 코드가 아니라 정책/프롬프트로
두면 이렇게 된다"는 일반 법칙**에 가깝다. AIOS가 지금 당장 고칠 수 있는 것은 자기 자신의
`hard_fail_reasons` 버그다 — OBaI를 보고서야 이게 얼마나 흔하고 위험한 패턴인지 확인했을 뿐이다.

---

## 4. Capability × 저장소 교차 매트릭스

각 행은 AIOS의 실측 상태(내가 직접 확인) → 5개 저장소 중 최선의 참조 구현 → 구체적 권고다.

### 4.1 Agent/외부 AI 게이트웨이

- **AIOS 실측**: MCP 서버, 에이전트 전용 API 표면이 **코드베이스에 0건**. `capability_tokens` 테이블은
  무관한 DevEngine 유물(dead schema). JWT는 jti/refresh/revoke 전무, 로그아웃은 no-op
  (`aios_auth_gateway.md` §1).
- **최선 참조**: QuantDinger `app/utils/agent_auth.py` — R/W/B/N/C/T 닫힌 스코프 enum, opaque
  hashed token(서버측 즉시 revoke 가능), 인간 JWT와 물리적으로 분리된 미들웨어, 자기 권한 상승
  경로 원천 차단(`/me/tokens`는 `agent_required`가 아니라 `login_required`로만 접근 가능).
- **2차 참조**: AgenticTrading — "에이전트 설정에는 credential/실행경로가 절대 못 들어간다"는
  구조적 분리 원칙, MCP 도구 스코프(`agents:register`, `runs:write` 등) capability 모델.
- **권고**: QuantDinger의 스코프 enum + opaque token 모델을 그대로 AIOS Agent Gateway 설계의
  출발점으로 삼는다. 단 AIOS는 기존 `capability_tokens` dead schema를 재활용할지 새로 설계할지
  먼저 결정해야 한다(둘 다 두면 3번째 병렬 섬이 생긴다).

### 4.2 Idempotency

- **AIOS 실측**: `src/core/idempotency.py`의 claim-first 패턴 자체는 견고하나 **실사용처가
  마켓플레이스 구매 1곳뿐**이고, `idempotency_keys` 테이블에 tenant_id/expires_at이 없어 무한
  누적된다. PAP-006(`paper_control`)은 훨씬 정교(요청 다이제스트 대조, FAILED도 재현)하지만
  이 역시 별도 컨텍스트에 갇혀 있다(`aios_auth_gateway.md` §3).
- **최선 참조**: QuantDinger `qd_agent_idempotency` — `(agent_token_id, method, route, key)`
  4중 UNIQUE + `request_hash` 불일치 409 + in-progress TTL(900초) 기반 고아 복구.
- **권고**: AIOS의 `idempotency_keys` 스키마에 QuantDinger의 4중 스코프 + request_hash 컬럼을
  추가하고, 이걸 라우터 공통 의존성(`src/api/contracts/idempotency.py`, 이미 L4 스펙에 설계돼
  있음)으로 승격해 신규 라우터가 각자 재발명하지 않게 한다. TTL 기반 stale 복구는 QuantDinger가
  AIOS의 기존 설계보다 한 걸음 더 나아간 부분이니 반영할 가치가 있다.

### 4.3 라이브 트레이딩 게이트

- **AIOS 실측**: `Executor.execute()`의 2단 하드 블록(`mode` + `is_paper_trading`/`is_sandboxed`)은
  **매우 견고**하다. 다만 "런타임 토글형 allowlist"가 아니라 "코드/ADR 변경 없이는 못 뒤집는" 방식이라
  QuantDinger·AgenticTrading류의 운영 유연성은 없다(`aios_auth_gateway.md` §5). **더 심각한 문제는
  게이트의 유연성이 아니라, 이미 있는 결정론적 kill switch 게이트(`make_foundation_pre_submit_gate`)가
  실행 루프에 배선되지 않아 ACTIVE 상태에서도 무시된다는 것**(`aios_runtime_execution.md` §6, 최우선
  리스크 1위).
- **최선 참조**: QuantDinger `quick_trade.py` — scope(T) → allowlist → idempotency 예약 →
  paper_only → 서버 kill switch → notional 예약(per-order/daily) 6단 체인, 각 실패가 별도 HTTP
  status. AgenticTrading `robinhood_live_service.py` — 순수함수 리스크 게이트(견적가 없으면 무조건
  거부, 수량 클램프, 공매도 금지) + 브로커 사전심사 반영 + env 킬스위치 3중 방어, 전 과정 감사 로깅.
- **권고**: **오픈소스를 보기 전에 먼저 할 일** — `background_loops.py:146-151`과
  `execution_deps.py:21`에 이미 존재하는 `make_foundation_pre_submit_gate()`를 실제로 주입한다.
  이건 코드 한 줄 배선 문제이지 설계 문제가 아니다. 그 다음에야 QuantDinger의 6단 체인·notional
  예약 개념을 AIOS의 mandate(`foundation/mandates`) 레이어에 결합하는 게 의미가 있다.

### 4.4 프로세스 토폴로지 / 워커 리스

- **AIOS 실측**: 단일 uvicorn 프로세스가 HTTP + 5개 트레이딩 루프를 전부 소유. **DB 레벨
  lease/heartbeat/소유권 레코드가 전무** — `scheduler.list_runnable()`이 조건 없이 RUNNING을 전부
  가져와, 인스턴스 2개를 띄우면 같은 실행을 중복 tick한다(`aios_runtime_execution.md` §1, 최우선
  리스크 3위).
- **최선 참조**: QuantDinger — API/Trading/Scheduler/Celery 역할별 독립 프로세스, `qd_strategy_
  runtime_leases`(`fencing_token`, 소유자 변경 시에만 +1) + `SKIP LOCKED` 커맨드 큐 + 재기동 시
  `restore_desired_strategies()`로 DB의 "의도한 상태"를 다시 로드. 리스 갱신 실패 시 **즉시 로컬
  중지**(계속 돌리다 이중 실행되는 것보다 멈추는 쪽 선택).
- **권고**: 이건 Phase 2B가 제안한 "Temporal 도입" 같은 큰 결정을 하기 전에, QuantDinger 수준의
  최소 버전 — `strategy_executions`에 `owner_id`/`fencing_token`/`heartbeat_at` 컬럼 3개를 추가하고
  `list_runnable()`에 소유권 조건을 거는 것 — 만으로 지금 당장의 다중 인스턴스 위험을 닫을 수 있다.
  Celery/Temporal 같은 별도 워커 프레임워크 도입은 그 다음 단계 논의로 미뤄도 된다.

### 4.5 주문 상태기계 / 브로커리지 추상화

- **AIOS 실측**: 상태 전이표는 완성돼 있으나(`oms/domain/state_machine.py`) 실행 경로가 호출하지
  않는 죽은 코드. DB CHECK 제약도 없다. Executor는 `OrderType.MARKET` 하드코딩, 부분체결은 상태만
  지원하고 실행 경로는 FILLED만 처리(`aios_runtime_execution.md` §2).
- **최선 참조**: LEAN — `Order` 필드 전체 `internal set`(엔진만 변경 가능), `IBrokerageModel`이
  "브로커 제약(Can*)"과 "시뮬레이션 모델(Get*Model)"을 한 인터페이스로 묶어 **백테스트가 라이브와
  동일한 제약·비용 모델을 강제로 재현**하게 만드는 것이 패리티의 핵심. `_openOrders`/`_completeOrders`
  분리, "이미 종료된 주문은 되돌리지 않는다"는 단조성 가드.
- **권고**: 지금 죽어 있는 `oms/domain/state_machine.py`를 살리는 것이 새 패턴을 들여오는 것보다
  먼저다. 그 위에 LEAN의 `IBrokerageModel` 번들링 아이디어(제약+비용모델을 하나의 인터페이스로) —
  이건 §4.7 백테스트/라이브 패리티와 직결된다.

### 4.6 Strategy IR / 마켓플레이스 신뢰

- **AIOS 실측**: 전략은 순수 AND/OR 평면 문자열(중첩·NOT 불가)로만 표현 가능. "조건트리 v2" AST는
  설계만 존재. 마켓플레이스 리스팅은 가격·상태뿐이고 provenance/검증리포트해시/risk envelope 노출
  없음. 위 §3.1의 죽은 `enterprise.py` 계약이 바로 이 갭의 답을 이미 갖고 있다.
- **최선 참조**: OBaI `StrategyDefinition` dataclass — indicators/entry_rules/exit_rules/
  position_sizing/risk_management/execution_config를 갖춘 완성도 높은 IR, `FILL_TIMING` 상수를
  결과에 동봉해 룩어헤드 부재를 결과 스스로 증명하는 방식, `validate()`가 미정의 지표 참조·
  타임프레임 불일치를 스키마 단계에서 정적 차단.
- **경고 사례(반면교사)**: AgenticTrading 마켓플레이스는 서명/출처 검증이 **전혀 없고 코드 주석이
  이를 "on purpose"라고 인정**한다("No whitelist, on purpose"). AIOS는 이 방향으로 가면 안 된다 —
  1차 Deep Dive(§5)가 이미 지적한 provenance/signature 요구사항이 AgenticTrading을 보고 나니 더
  분명해졌다.
- **권고**: 죽은 `enterprise.py` 계약을 살려 `strategy_listings`와 연결하고, OBaI의 `validate()`
  스타일 정적 검증을 조건식 파서에 추가한다. Sigstore/in-toto(1차 Deep Dive가 이미 지목)는 이
  IR이 실제로 배선된 다음 단계 문제다.

### 4.7 백테스트/라이브 패리티, 검증 게이트

- **AIOS 실측**: `foundation/backtest`가 실행 루프와 엔진을 공유하지 않는다. 검증 체크는 6개 중
  1개(backtest)만 동작, DSR/PBO/walk-forward는 수식만 있고 코드 0줄(`aios_strategy_lifecycle.md`
  §3).
- **최선 참조**: 세 저장소가 다른 각도에서 같은 답을 준다 — **QuantDinger**(백테스트와 라이브가
  동일 컴파일 산출물 + 다른 브로커 어댑터만 교체), **LEAN**(`IBrokerageModel`이 제약+비용모델을
  번들링해 강제), **Freqtrade**(`LocalTrade`/`Trade`로 동일 도메인 코드를 DB 유무만 다르게 재사용,
  게다가 `lookahead-analysis`/`recursive-analysis` CLI로 룩어헤드·재귀편향을 **자동 검출하는 도구**
  까지 갖춤).
- **권고**: "백테스트 엔진과 실행 루프가 같은 전략 프로그램을 공유해야 한다"는 원칙을 아키텍처
  결정으로 못박는다. DSR/PBO는 1차 Deep Dive가 이미 P0로 지목했으니 반복하지 않되, Freqtrade의
  lookahead-analysis류 자동 검출 도구는 새로운 발견 — AIOS 백테스트 CI 게이트에 추가할 후보다.

### 4.8 리컨실리에이션 / 리스크 프로텍션

- **AIOS 실측**: 3-way 대사 도메인은 잘 설계돼 있으나(`classify_item`, kill switch 연동) **주기적으로
  도는 대사 워커가 없다** — HTTP 라우터 트리거만 존재. RiskEngine 9지표는 fail-closed로 잘 구현됨.
- **최선 참조**: LEAN — 시작 시 1회 풀 리컨실 + 라이브 중 매일 자동 재동기화 + 10초 후 자체 검증
  + 연속 5회 실패 시 강제 중단이라는 4단 방어선. Freqtrade `plugins/protections/` — StoplossGuard/
  MaxDrawdown/CooldownPeriod/LowProfitPairs를 `ProtectionReturn(lock, until, reason, side)`라는
  설명 가능한 잠금 객체로 표준화(단, 이건 진입 게이트일 뿐 AIOS의 RiskEngine 같은 실시간 kill
  switch는 아님 — Freqtrade 자체에도 없는 것으로 확인됨).
- **권고**: LEAN의 "정기 재동기화 + 자체 검증 + 연속실패 중단" 패턴을 AIOS의 기존 3-way 대사 도메인
  위에 주기 실행 루프로 얹는다(이미 있는 도메인 로직을 배선하는 문제 — §3.1과 같은 유형).

---

## 5. 우선순위 재정렬 — "코드 배선" > "패턴 도입" > "새 인프라" (초안, §7에서 갱신됨)

> 아래는 코드 교차검증 직후의 초기 판단이다. [연구 결정 기록](AIOS_Codex_Fable_CrossReview_Research_Direction_Record_2026-09-03.md)이
> "Tier 0을 지금 구현하라"는 결론은 채택하지 않고 P0-R 연구 항목으로만 등록했다 — §7을 최신
> 결론으로 본다. 이 섹션은 "코드 레벨로 보면 무엇이 배선 문제이고 무엇이 새 설계가 필요한
> 문제인지" 구분한 기록으로서 그대로 남긴다.

세 조사(내부 3건 + 외부 4건)를 다 놓고 보면 권고가 자연스럽게 3계층으로 정렬된다.

### Tier 0 — 오픈소스와 무관, 지금 코드만으로 끝나는 것 (블라스트 반경 최대, 비용 최소)
1. `make_foundation_pre_submit_gate()`를 `background_loops.py`·`execution_deps.py`에 배선 (§4.3)
2. `DataDistrustMonitor`를 scheduler/tick에 배선
3. `strategy_executions`에 `owner_id`/`fencing_token`/`heartbeat_at` 추가 + `list_runnable()` 소유권 조건 (§4.4)
4. 자기 자신의 `hard_fail_reasons` 항상-빈-튜플 버그 수정 (§3.2)
5. 죽어 있는 `src/contracts/enterprise.py` StrategyPackage를 마켓플레이스 리스팅에 연결 (§3.1, §4.6)
6. 죽어 있는 `oms/domain/state_machine.py`를 `order_service`에 배선 (§4.5)

### Tier 1 — 오픈소스 패턴을 AIOS 기존 구조 위에 얹는 것
7. QuantDinger 스코프 enum(R/W/B/N/C/T) + opaque token → Agent Gateway 설계 (§4.1)
8. QuantDinger idempotency 4중 스코프 + request_hash → 기존 `idempotency_keys` 확장 (§4.2)
9. LEAN `IBrokerageModel` 번들링 → 백테스트/실행 공유 아키텍처 (§4.7)
10. LEAN 4단 리컨실 방어선 → 기존 3-way 대사 도메인에 주기 실행 추가 (§4.8)
11. OBaI `StrategyDefinition.validate()` 스타일 정적 검증 → 조건식 파서 (§4.6)
12. Freqtrade lookahead/recursive-analysis류 자동 검출 도구 → 백테스트 CI 게이트 (§4.7)

### Tier 2 — 새 인프라 도입 논의 (Phase 2B, 규모가 크므로 별도 결정 필요)
13. Temporal/유사 durable workflow (Tier 0-#3로 당장의 위험은 닫히므로 급하지 않음)
14. OPA/Cedar 등 범용 정책 엔진 (Tier 0-#1, #2가 이미 결정론적 게이트의 배선 문제임을 보여줌 —
    엔진 자체보다 "배선을 빠뜨리지 않는 프로세스"가 더 급한 문제일 수 있다는 재고 필요)
15. Sigstore/in-toto/SLSA, gVisor/Firecracker (§4.6의 IR/서명이 먼저 배선된 뒤에야 의미가 생긴다)

이 정렬이 1차 Deep Dive·2차·2차-B의 결론을 뒤집는 것은 아니다. 다만 "무엇을 먼저 할지"에 대해
2차-B가 제안한 8개 plane, 1차가 제안한 10개 plane 중 어느 것도 Tier 0의 6개 항목만큼 즉각적이지
않다는 것이 코드 레벨 검증의 결론이다.

---

## 6. Phase 2 / Phase 2B 후보 재평가 (32개 → 다음 스텝)

[검토 의견서](AIOS_Codex_Research_Review_by_Fable_v1_2026-09-03.md) §5에서 제안한 기준
("(a) 실제 결함과 대응하는가 (b) 개인 프로젝트 수준을 넘는가")을 이제 적용할 수 있다.

- **이미 코드 레벨 검증 완료, 추가 조사 불필요**: QuantDinger, LEAN, AgenticTrading, OBaI, Freqtrade
  (이 문서 §4가 전부 반영).
- **Phase 2B 중 여전히 유효한 후보**(§4의 결함과 직접 대응하지만 아직 코드 레벨 미검증):
  Temporal(Tier 2-#13), OPA(Tier 2-#14), Sigstore/in-toto/SLSA(Tier 2-#15) — 다만 위 순서대로
  Tier 0/1이 끝난 뒤에.
- **Phase 2 금융/에이전트 15개 중 재검토 가치가 있는 것**: `yogeshg665/quill-trading-agent`
  ("Independent Risk Guardian" — AI 판단과 execution authorization 분리라는 개념이 지금까지
  본 5개 저장소 중 명시적으로 다룬 곳이 없다. AIOS의 RiskEngine은 이미 이 분리를 갖고 있지만,
  "AI가 제안하고 별도 trust domain이 거부권을 갖는다"는 프레이밍 자체를 문서화하는 데 참고할
  가치는 있다). 나머지 14개는 이번 5개 저장소가 이미 다룬 capability(Agent Gateway/마켓플레이스/
  백테스트/실행)를 반복할 가능성이 높아 우선순위가 낮다.
- **나머지 Phase 2 후보**: 코드 레벨 조사를 보류하고, Tier 0/1이 실제로 AIOS 코드에 반영된 뒤
  남는 갭을 기준으로 재선별할 것을 권한다 — 표를 먼저 늘리지 않는다는 이번 검토의 원칙을 그대로
  유지한다.

---

## 7. Decision Record 반영 — P0-R 레지스트리 대응

[연구 결정 기록](AIOS_Codex_Fable_CrossReview_Research_Direction_Record_2026-09-03.md)이 정한 역할
분담(Codex=외부 탐색, Fable=내부 감사/red-team, ChatGPT=교차검증·종합)과 성숙도 사다리
(DISCOVERED→SCREENED→RELEVANT→CODE-VERIFIED→FAILURE-ANALYZED→AIOS-MAPPED→POC-CANDIDATE→
ADR-CANDIDATE), 그리고 "당분간 `aios` 프로덕션 코드 수정 보류"를 그대로 받아들인다. §5의
Tier 0 목록은 **구현 지시가 아니라 P0-R 레지스트리에 대한 증거 제출**로 재해석한다.

### 8.1 P0-R1~R3 — 이미 CODE-VERIFIED 단계까지 채워져 있음

이 문서 §4.3·§4.4가 제시한 file:line 증거가 Decision Record의 P0-R1(Execution Ownership),
P0-R2(Foundation Pre-submit Gate Authority), P0-R3(DataDistrust Enforcement)와 정확히
같은 대상을 가리킨다. 즉 세 항목 모두 최소한 **CODE-VERIFIED**(실제 구현 확인) 단계는
이미 충족했고, 아래 §7.2가 FAILURE-ANALYZED 단계에 필요한 자료를 보탠다.

### 8.2 P0-R4 — Fail-open 경로 목록 (지금까지 확인된 것)

Decision Record가 요구하는 "어떤 privileged path가 gate 없이 통과하는가"를 지금까지의
감사에서 모은 것만 정리하면:

| 경로 | Fail-open 지점 | 근거 |
|---|---|---|
| 실행 루프 신규 주문 제출 | `pre_submit_gate=None` → `is_submission_allowed(None,...)`가 무조건 허용 | `aios_runtime_execution.md` §6, `background_loops.py:146-151`→`scheduler.py:74`→`tick.py:326-333`→`pre_submit_check.py:36-37` |
| 실행 시작(`ExecutionService`) | `pre_start_gate` 두 생성 지점 모두 미주입 | `src/api/execution_deps.py:21`, `background_loops.py:115` |
| 이상 시세 방어 | `distrust_monitor=None` → 검사 블록 스킵, `distrust_level` 영구 NORMAL | `tick.py:196-197, 224-235` |
| (참고, 외부) QuantDinger 레이트리밋 | Redis 실패 시 in-memory fallback — gunicorn 멀티워커에서 카운터 분리로 실질 한도 초과 허용 | `ext_quantdinger.md` §2, `_memory_rate_limit` |
| (참고, 외부) LEAN 미지 주문 이벤트 | `TryGetOrder` 실패 시 로그만 남기고 이벤트 드롭(예외 전파 없음) | `ext_lean.md` §2.3 |

AIOS 내부 3건은 전부 "게이트 객체가 `None`으로 생성 지점에서 누락"이라는 **동일한 배선 실수
패턴**이다 — 세 곳이 독립적인 버그가 아니라 "생성자에 게이트를 필수 인자로 강제하지 않는
설계"라는 하나의 근본 원인일 가능성이 높다는 게 새로 추가되는 관찰이다. P0-R5(Authority
Wiring Proof) 설계 시 "게이트 인자를 Optional로 두지 않는다"는 타입 레벨 불변조건 자체를
증명 대상에 포함할 것을 제안한다.

### 8.3 P0-R5에 대한 제안 — evidence를 무엇으로 삼을 것인가

Decision Record는 "실제 강제됨을 증명할 방법을 설계하라"고만 하고 방법은 열어뒀다. 이번
조사에서 참고할 만한 두 가지 외부 사례:
- LEAN은 `IBrokerageModel.CanSubmitOrder`를 **모든** 제출 경로가 동일 지점(`BrokerageTransactionHandler.
  HandleSubmitOrderRequest`)에서 호출하도록 강제해, "게이트를 우회하는 두 번째 제출 경로"가
  구조적으로 존재하지 않는다(`ext_lean.md` §2.2, §3.4).
- QuantDinger는 감사로그(`qd_agent_audit`)가 **거부 응답을 포함한 모든 분기**에서 기록되므로,
  "게이트가 통과시켰다"와 "게이트가 아예 호출되지 않았다"를 사후 로그로 구분할 수 있다
  (`ext_quantdinger.md` §10).
AIOS가 채택할 만한 최소 evidence 형태: (1) 정적 검사 — 모든 실행 루프/서비스 생성 지점을
grep해 게이트 인자가 non-None임을 CI에서 단언, (2) 동적 검사 — safety_control을 ACTIVE로 만든
상태에서 제출을 시도하는 적대적 테스트가 항상 거부되는지 확인(`tests/adversarial/`에 이미
있는 패턴 확장), (3) 감사로그 — 게이트 평가 자체가 없었던 제출은 별도 심각도로 표시.

### 8.4 다음 저장소 조사에 대한 입장 변경

이전 §7(구 버전)에서 "Quill을 볼지 말지, 아니면 전면 보류할지"를 물었던 것을, Decision Record의
성숙도 사다리에 맞춰 재정리한다: 신규 저장소 조사(Quill 포함)는 Codex의 Horizontal Research로
계속 진행해도 되지만, **AIOS-MAPPED 이상으로 승격하려면** 이 문서 §4와 같은 수준의 코드 인용이
필요하다는 기준을 명시적으로 요구한다. 이번 5개 저장소가 이미 CODE-VERIFIED 이상이므로, 이들을
가리키는 P0-R 항목 검증을 다음 우선순위로 두고 신규 저장소는 그 뒤에 이어간다.

## 8. 코덱스에게

- §7.2의 관찰(3개 fail-open이 "게이트 인자 Optional 허용"이라는 하나의 근본원인일 가능성)에
  동의하는지, 그리고 이걸 P0-R5의 증명 대상(타입 레벨 불변조건)에 포함하는 것에 동의하는지.
- §7.3에서 제안한 3가지 evidence 형태(정적 검사/적대적 테스트/감사로그 분리) 중 어느 것을
  먼저 설계할지, 혹은 ChatGPT의 교차검증 라운드에서 다르게 정할지.
- Decision Record §6이 정한 역할 분담대로, 나는 앞으로도 `aios` 코드를 고치지 않고 이 저장소의
  research 산출물만 갱신한다 — 이 이해가 맞는지 확인 부탁한다.
