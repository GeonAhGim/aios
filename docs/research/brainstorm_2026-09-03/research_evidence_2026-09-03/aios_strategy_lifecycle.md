# AIOS 전략 라이프사이클 구현 현황 감사 (2026-09-03 기준)

대상: C:/aios/aios (Python/FastAPI). 비교축: QuantDinger(template→compile→immutable
version→deployment), OBaI(strategy JSON + iterative backtest + OOS), AgenticTrading
(marketplace metadata), LEAN(algorithm framework).

## 1. 전략 정의 (IR / manifest / 저장·버전·불변성)

**구현됨**
- FSM 기반 전략 IR: `src/data/models/strategy_fsm.py:13-44` (`FSMState` 6종,
  `FSMTransition`, `FSMStrategyConfig{strategy_id, version, target_asset, market,
  exchange, states, transitions, author_agent, memory_provenance}`).
- 조건식은 컴파일된 **평면 문자열**(`"{indicator}{params} {op} {threshold}"`를
  `AND`/`OR`로만 결합) — `src/services/condition_compiler.py:41-73`,
  평가는 `src/core/strategy/condition_evaluator.py:48-101`(정규식 1개로 파싱).
  중첩/NOT/괄호 불가 — 순수 AND 또는 순수 OR만 지원.
- 저장은 **DB row**(JSON 텍스트 아님): `strategies` 테이블 `fsm_definition JSONB`
  — `src/db/migrations/versions/f6335fdcbe80_strategies.py:26-51`. PK는
  `(strategy_id, version)` 복합키 — version은 문자열(`"v1.4"` 등) 자유 형식.
  파일 기반 로더도 별도 존재(`src/core/loader/strategy_loader.py:15-21`,
  `FSMStrategyConfig.model_validate_json`)하지만 실제 저장 경로는 DB.
- 저장 API: `StrategyBuilderService.save_strategy` — `src/services/
  strategy_builder_service.py:92-131` (INSERT, 중복 strategy_id/version 거부).
- 생애주기(state machine): `lifecycle_status` CHECK 제약 15개 값(IDEA→...→
  RETIRED/REJECTED/FAILED), `src/db/migrations/versions/f6335fdcbe80_strategies.py:35-40`.
  전이 강제는 `LIFECYCLE_ORDER` 순차 전용, 건너뛰기 불가 —
  `src/services/strategy_builder_service.py:39-51, 169-235`(조건부 UPDATE로
  동시성 충돌 방지). `assert_executable()`(238-243행)이 실행 가능 상태
  (`APPROVED/DEPLOYED/MONITORING`)를 강제.
- 구조 검증기: `src/core/validator/strategy_validator.py:15-48` — 고아 state,
  자기순환, 중복 transition, initial_state 미선언을 탐지(순수 그래프 검증,
  조건식 의미는 검증하지 않음).

**부분 구현**
- **버전 불변성/content hash**: `strategies` 테이블 자체에는 해시·서명 컬럼이
  없음. content hash는 검증 파이프라인에만 존재 —
  `strategy_validation_run.input_snapshot_hash`/`strategy_validation_result.
  result_hash` (`src/foundation/validation/domain/rules.py:32-63`, sha256).
  즉 "이 버전이 검증됐을 때 입력이 무엇이었는가"는 해시로 고정되지만,
  전략 정의 자체(`fsm_definition`)를 언제든 같은 (strategy_id, version)에
  UPDATE로 덮어쓰는 것을 막는 DB 제약은 없음 — `save_strategy`가 중복
  삽입만 막을 뿐, "버전은 한 번 저장되면 불변"이라는 원칙이 코드로
  강제되지 않음(애플리케이션 계층에 UPDATE 경로가 없다는 관례에만 의존).
- **콘텐츠 주소화 아티팩트(artifact_hash)**: `src/contracts/enterprise.py:153`
  (`StrategyArtifact.content_hash`)과 `src/services/paper_strategy_projection.py`
  에 `sha256:` 패턴의 `artifact_hash`/`content_hash` 필드를 가진 완결된
  "enterprise StrategyPackage" 계약(hypothesis, risk_envelope, dependencies,
  validation_refs, license_ref 포함)이 존재 — QuantDinger/AgenticTrading이
  기대하는 매니페스트와 형태가 거의 일치. 그러나 **어떤 API 라우터도 이를
  호출하지 않고, DB 테이블도 없으며, 유닛테스트(`tests/unit/
  test_paper_strategy_projection.py`) 외에는 아무 호출자가 없음** — 순수
  변환 함수로만 존재, 파이프라인에 배선되지 않은 죽은 코드에 가깝다.

**설계만 존재**
- "조건트리 v2"(`GRAMMAR_VERSION="cond-v2"`): AST(`AndNode/OrNode/NotNode`),
  파서, `indicator_key.py`, `state_memory.py`, `tree_evaluator.py`,
  `confidence.py`, `risk_params.py` — 전부 `docs/specs/
  L4_strategy_portfolio_backtest_v1.0.md` §2.1(56-70행)에 "[신규]"로만
  존재. 실제 파일 없음(확인: `src/core/strategy/condition_ast.py` 등 8개
  파일 전부 미존재). 지표 레지스트리(`src/core/indicators/spec.py`,
  `registry.py`)도 동일 — 현재는 `talib_adapter.py` 하나뿐, 파라미터
  범위검증·정확한 lookback 계산 없음(§1.2 표, 47행).

**없음**
- 서명(디지털 서명)/코드사인 — 저장소 전체에 strategy용 signature 개념 없음.
- 패키지 포맷(zip/wheel 유사 배포 단위) — 없음. DB row가 유일한 표현.

## 2. AI 저작 경로 (LLM strategy authoring) / 사용자 코드 샌드박스

**구현됨**
- 목표기반 마법사(비-AI): `src/services/strategy_wizard_service.py` —
  3(목표)×3(위험허용도) 순수함수 템플릿(9종), RSI/MACD/Williams%R/CCI/
  Stochastic 조건 조합을 결정론적으로 생성(53-147행). AI 호출 없음.
- ADR: `docs/design/ADR-2026-08-29-wallet-marketplace-dual-seller-
  strategy-authoring.md` — §1 지갑, §2 seller_type 이원화, §3 마법사
  전부 "구현 완료"로 표시.

**부분 구현**
- 자연어 프롬프트 → 전략 생성(LLM 경로): `src/services/
  strategy_prompt_service.py:26-31` — 라우터·스키마·반환 타입은
  최종 형태로 구현돼 있으나, `generate()`가 항상
  `PromptGenerationUnavailableError`(501)를 던짐 — Anthropic API 크레딧
  $0라 실제 LLM 호출 코드 자체가 아직 없음(정직한 스텁, 가짜 응답 없음).

**없음 (해당사항 아님)**
- **사용자 코드 실행/AST 샌드박스**: 애초에 전략이 "임의 실행 코드"가
  아니라 선언적 조건식 문자열(위 §1)이므로 이 시스템에는 사용자가 작성한
  코드를 실행하는 경로 자체가 없음 — LEAN/QuantConnect류의 코드 샌드박스
  개념이 구조적으로 불필요. 다만 이는 동시에 "조건식 문법이 매우
  제한적"이라는 §1의 한계와 표리관계.

## 3. 검증/승격 게이트 (backtest, OOS, DSR/PBO, PAPER→LIVE 승인)

**구현됨**
- 백테스트 엔진(단일 종목, event replay): `src/foundation/backtest/
  application/run_backtest.py:67-179` — bar 순회, look-ahead 방지
  (`simulate_fill.py:1-9`, 항상 "신호 다음 bar 시가"로 체결), `warmup_bars`
  검사(`domain/rules.py:29-32`), 비용모델(선형 fee_bps+slippage_bps만,
  `domain/models.py:16-25`).
- 지표: Sharpe/Sortino/MDD/승률/turnover — `compute_metrics.py:69-114`.
  표본 부족·표준편차 0이면 조용히 0이 아니라 `None`을 반환(76번 "bare
  float 금지" 원칙 실제 반영, 27-47행).
- 검증 파이프라인 골격: `src/foundation/validation/application/
  start_validation.py:74-195` — `input_snapshot_hash`로 멱등성/캐시 재사용
  (STR-001/STR-007, 103-149행), `strategy_validation_run/result` 테이블에
  영속(`3b244535b311_strategy_validation.py`), 검증결과 append-only
  (`REVOKE UPDATE, DELETE`, 86행).
- PAPER→LIVE 승인: `src/services/execution_service.py` — mode=LIVE는
  자동화 수준과 무관하게 항상 Critical Risk 승인 요구(9-13행), 승인은
  `src/core/approval/service.py`(SOLO/DUAL 서명, mandatory_wait_seconds).
  PAPER 실행 이력은 종료하지 않고 별행 LIVE 생성(`converted_from_
  execution_id`, execution_service.py 25-31행) — "가상→실제 마법적 전환"
  경로 자체를 차단.

**부분 구현**
- 검증 체크는 **6개 중 1개**(`check_type="backtest"`)만 실제 계산 —
  `docs/design/codex/76_...v1.0.md` §3이 요구하는 point-in-time/OOS/
  robustness/stress/failure-conditions 5개는 없음(마이그레이션 docstring
  자인, `3b244535b311_strategy_validation.py:18-21`).
- `hard_fail_reasons`가 **구조적으로 항상 빈 튜플**
  (`start_validation.py:180`, `domain/rules.py:66-75`) → FAIL 판정이
  현재 코드로는 발생 불가(PASS 또는 PASS_WITH_OBLIGATIONS만 가능).
  즉 "게이트"는 있으나 실제로 무언가를 막을 수 있는 조건이 없음.
- 승인은 존재하지만 **누가 승인하는지에 대한 신원 검증이 미완**: DUAL
  서명의 2번째 서명자는 시스템에 사용자로 매핑되지 않아 admin 전용
  경로로만 처리(`src/core/approval/service.py:18-24` docstring).

**설계만 존재 (docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.4/§2.5 확인)**
- Walk-forward/OOS 분할: `splits.py`(anchored/rolling, purge/embargo) — 미존재.
- **Deflated Sharpe Ratio·PBO(CSCV)**: `domain/overfitting.py` — 수식만
  §3.5(372-377행)에 정의, 코드 0줄. 저장소 전체 grep 결과 DSR/PBO 구현 0건.
- 파라미터 안정성(`param_stability.py`), 파라미터 스윕(`param_sweep.py`),
  스트레스 시나리오(`stress.py`, 비용×2/×3·최악 N일 제거 등) — 전부 미구현.
- 검증 정책 버전화(`ValidationPolicy`, `policy_hash`), 아티팩트 해시
  기반 검증(`build_artifact`/`verify`, `INTEGRITY_ARTIFACT_HASH_MISMATCH`)
  — 미구현. 현재는 `strategies.fsm_definition`을 매번 직접 읽어 검증(버전
  불변성·재현성 근거 약함, §1.2 44행 자체 인용).
- Survivorship bias 처리(`universe.py`), leakage 관련 이벤트 구동
  재작성(`event_loop.py`, O(n²) 지표 재계산 제거) — 미구현.
- 감사 인용(`docs/FULL_AUDIT_2026-09-02.md`, L4 §1.2 38-48행) 자체가 이미
  "재생이 아니라 재실행", "R1·R3·R4·R5 전부 미충족"이라고 자평.

## 4. 배포 모델 (deployment ↔ strategy version 바인딩)

**구현됨 (실사용 경로)**
- `strategy_executions` 테이블 — `mode ∈ {PAPER,LIVE}`, `status ∈
  {PENDING_APPROVAL,RUNNING,PAUSED,RETIRED}`, `(strategy_id, version)` FK
  — `src/db/migrations/versions/f2a3b4c5d6e7_strategy_executions.py:21-46`.
  주문/포지션에 `execution_id` FK로 연결(49-53행) — **이 경로가 전략
  버전↔배포↔계정 바인딩의 실제 구현**.
- 실행 루프: `src/services/execution_loop/tick.py` — `execution_id`로
  `strategy_executions`를 로드해 FSM 상태·시장데이터·리스크게이트를 매
  틱 통과(57-352행 다수). Watchdog/Circuit breaker의 `paused_by=
  SAFETY_LAYER`가 사용자의 재시작보다 우선(execution_service.py 19-23행
  주석).
- `StrategyEngine.evaluate` (FROZEN_PAPER_ONLY): 조건 매칭 → `Signal`
  생성만, 수량 결정은 `PortfolioEngine.allocate`(8.2-A Master Authority
  원칙, `src/core/strategy/engine.py:1-9` 명시).

**부분 구현 / 이중 구현(미배선)**
- `src/foundation/paper_control/**` — 별도의 더 "기관급" 배포 상태기계
  (`DeploymentState: REQUESTED→READY→RUNNING→PAUSED→STOPPED/FAILED/
  DEGRADED/RECOVERY_REVIEW`, `fence_token`, `mandate_revision_id`,
  `AdapterProvenance`) — `domain/models.py:13-93`. `start_deployment.py`/
  `resume_deployment.py`가 risk_gate(`GateKind.DEPLOYMENT`) 재평가를
  거치는 등 훨씬 정교한 게이트를 가짐(request_deployment.py 등). **그러나
  이 경로의 `package_ref`는 불투명 문자열일 뿐**(`request_deployment.py:
  10-13` "package 유효성을 실제로 검증하지 못한다"고 자인) —
  `strategies`/`strategy_executions` 테이블과 FK로 연결되지 않음.
  `src/services/execution_loop/tick.py`는 이 경로를 전혀 참조하지 않음
  (grep 결과 `paper_control`은 `src/api/routers/foundation/
  paper_control.py`·`src/api/foundation_deps.py`에서만 등장) — 즉
  **실제 실행 틱과 무관한, API만 노출된 병렬 상태기계**로 보인다.
- Portfolio mandate(`src/foundation/mandates/**`)도 전략/실행 계층이
  아직 소비하지 않음(`domain/models.py:87-89` 자체 주석 "전략/실행
  계층은 아직 이 계약을 소비하지 않음"). `core/portfolio/engine.py`는
  mandate를 전혀 참조하지 않음(L4 §1.2 43행 자체 지적).

**없음**
- Fence token을 이용한 이중배포 방지가 `strategy_executions` 경로에는
  없음(paper_control 경로에만 존재하나 미배선).

## 5. 마켓플레이스 (listing/purchase/verification/dispute/wallet)

**구현됨**
- 리스팅: `strategy_listings(strategy_id, version, seller_user_id, price,
  status)` — `e5f6a7b8c9d0_strategy_listings_purchases.py:33-47`. 상태
  `DRAFT→PENDING_VERIFICATION→LISTED/DELISTED`.
- 소유자만 리스팅 가능(`ListingService.create_listing`,
  `src/services/listing_service.py:69-103`), 3개월 Paper Trading 이력
  콜백 검증(`submit_for_verification`, 136-159행 — 실제 이력 추적 로직은
  DI로 외부 주입, 자체 구현 아님).
- 수동 검증: `VerificationService.decide` — 승인/거부, 이해상충 방지
  (검증자≠판매자, `verification_service.py:82-86`), 동시처리 충돌 방지
  (조건부 UPDATE, 64-112행). **자동 사기탐지·오버피팅 리포트 연동
  없음** — 검증은 순수 수동 플래그 전환(사람이 체크리스트를 보고
  APPROVE/REJECT 누르는 것 뿐, 시스템이 `strategy_validation_result`를
  참조하지 않음).
- 구매: `PurchaseService`(코드는 미열람이나 라우터 확인) + `Idempotency-
  Key`(`src/api/routers/marketplace.py:133-163`, 사용자 스코프 키).
  지갑 기반 결제(플랫폼 내부 크레딧, `ADR-2026-08-29-...md` §1) — PG
  미연동, 수동 충전.
- 구매↔실행권한 분리: `StrategyAccessService.can_access` —
  소유자이거나 `payment_status='CONFIRMED'`인 구매만 FSM 정의 열람 가능
  (`src/services/strategy_access_service.py:46-70`). **구매 자체가 곧
  실행권한**(추가 리스크/적합성 게이트 없음) — `assert_executable()`이
  체크하는 것은 전략의 전역 lifecycle_status뿐, 구매자별 별도 승인
  단계는 없음.
- 판매자 이원화(`seller_type ∈ {USER,PLATFORM}`), 분쟁(`DisputeService`),
  판매자 정지(`seller_suspended`) — 마이그레이션·서비스 존재.

**부분 구현**
- 리스팅 메타데이터가 매우 얇음: `strategy_id/version/seller_user_id/
  price/status/seller_type`뿐 — provenance(작성자 신원 증빙), 검증
  리포트 해시, risk envelope, supported markets 등은 리스팅 자체에는
  없고 `strategies` 테이블의 `market/exchange/target_asset/risk_level/
  certified_badge` 컬럼에 흩어져 있음(조인 필요, 통합 뷰 없음).
- `certified_badge`/`last_recertified_at` 컬럼은 존재(f6335fdcbe80,
  41-42행)하나 자동 재인증 절차(`14_marketplace_detailed_v1.1.md` §14.3)
  구현 확인 못함(관련 서비스 코드 미발견 — grep 시 이 필드를 실제로
  갱신하는 코드 경로가 안 보임).

**없음**
- AgenticTrading류 "리스팅에 검증 보고서 해시를 바인딩"하는 구조 없음 —
  구매자는 `strategy_validation_result`를 리스팅 화면에서 볼 수 있는
  API가 확인되지 않음(마켓플레이스 라우터에 그런 엔드포인트 없음).

## 6. 성과 보고 (Performance Statement)

**구현됨** (`docs/specs/L4...` §1.2가 "디렉터리 없음"이라 적었던 시점 이후
새로 구현된 것으로 보임 — 문서가 코드보다 뒤처짐)
- `src/foundation/performance/**` 전체 존재: contracts/domain(twr, mwr,
  identity, risk_metrics, methodology)/ports/adapters/application.
- `compute_statement()` — `src/foundation/performance/application/
  compute_statement.py:75-195`. WORM 저장(revision 체계, `next_revision`),
  회계 항등식 검증 골격(`identity_ok`/`identity_residual`), evidence
  이벤트 기록(122-157행).
- 정직한 한계 명시: 스냅샷이 항상 1개뿐이라 TWR/MWR·항등식 계산
  불가 시 `None`(PENDING) 유지, 0으로 대체 금지(`_gross_pnl` 42-72행,
  `_INSUFFICIENT_VALUATION_LIMITATION`/`_MISSING_LEDGER_LIMITATION` 상수).

**부분 구현 / 갭**
- **전략 버전과의 연결 부재**: `StatementScope ∈ {PAPER, LIVE}`뿐이고
  `scope_ref`는 `paper_input_adapter.py`에서 **user_id(tenant)** 로
  취급(5, 79-146행) — 즉 성과 명세서는 **계정/테넌트 단위**이지
  `(strategy_id, version)` 또는 `execution_id` 단위가 아님. 특정 전략
  버전의 실측 성과를 별도로 조회하는 API/데이터 모델이 확인되지 않음.
- fees/slippage/funding/fx/estimated_tax는 원장에 컬럼이 없어 항상
  `None`(컴포넌트 분해 불완전, 46-49행).

## Gap Summary (설계 문서 대비 핵심 격차)

1. **조건식 표현력**: 순수 AND/OR 평면 문자열뿐, 중첩/NOT/괄호 불가 —
   "조건트리 v2"(cond-v2 AST)는 설계 문서에만 존재, 코드 0줄.
2. **과최적화 통제 전무**: DSR/PBO/walk-forward/OOS 분할/파라미터
   안정성 — 전부 미구현. `hard_fail_reasons`가 구조적으로 항상 빈
   튜플이라 검증이 FAIL을 낼 수 없음(사실상 게이트가 열려 있음).
3. **검증 체크가 6개 중 1개**: point-in-time·robustness·stress·
   failure-conditions 없음, 정책 버전화(policy_hash)·아티팩트 해시
   기반 재현성 검증 없음.
4. **배포 경로 이원화·미배선**: 실제 실행 틱(`tick.py`)이 쓰는
   `strategy_executions`와, 훨씬 정교하지만 `package_ref`가 불투명
   문자열이라 전략과 연결 안 되는 `foundation/paper_control`
   상태기계가 서로 배선되지 않은 채 병존. mandate(`foundation/
   mandates`)도 core 실행 엔진이 아직 참조하지 않음.
5. **엔터프라이즈 StrategyPackage 계약은 죽은 코드**: content_hash·
   risk_envelope·hypothesis·validation_refs·license_ref를 가진
   완결된 계약(`src/contracts/enterprise.py`)이 존재하지만 어떤
   라우터/DB/파이프라인에도 연결되지 않고 유닛테스트에서만 호출됨.
6. **마켓플레이스 리스팅 메타데이터 빈약**: provenance·검증 리포트
   해시·risk envelope가 리스팅 자체에 노출되지 않음(수동 검증만
   존재, 백테스트 결과와 연동되지 않음). 구매=실행권한이 즉시
   동일시되어 별도의 구매자별 리스크/적합성 재확인 단계 없음.
7. **성과 명세서-전략 버전 미연결**: Performance Statement는 계정
   단위(scope_ref=user_id)일 뿐, 특정 (strategy_id, version) 또는
   execution_id에 귀속된 성과 리포트를 조회하는 경로가 없음.
8. **버전 불변성 미강제**: `strategies` row에 해시/서명 컬럼이 없고,
   "저장 후 절대 수정 안 함"이 코드 제약이 아니라 관례에 의존.
9. **LLM 저작 경로는 스텁**: 라우터/스키마는 완성됐으나 실제 Claude
   호출 코드가 없음(크레딧 $0로 인한 501 스텁, 정직하게 명시됨).
10. **AI 코드 샌드박스는 구조적으로 불필요하지만 그만큼 표현력도
    제한적** — 사용자 코드 실행 경로 자체가 없어 QuantConnect식
    자유도(임의 Python 알고리즘)와는 근본적으로 다른 모델.

## 15줄 요약

AIOS는 전략을 자유 코드가 아니라 선언적 FSM+평면조건식(JSON, DB의 `strategies.fsm_definition`
JSONB)으로만 정의하며, `strategy_id+version` 복합키로 저장하되 해시/서명에 의한 불변성 강제는
없다. AI 저작은 목표기반 마법사(구현 완료, 비-AI 순수함수)와 자연어 LLM 프롬프트(크레딧 $0로
501 스텁) 두 축이 있고, 사용자 코드 실행이 아예 없어 AST 샌드박스 개념 자체가 불필요하다.
검증 파이프라인(`foundation/validation`)은 6개 예정 체크 중 backtest 1개만 실제 동작하며,
`hard_fail_reasons`가 구조적으로 항상 빈 튜플이라 FAIL 판정이 불가능하다 — DSR/PBO/
walk-forward/OOS/스트레스 테스트는 `docs/specs/L4_strategy_portfolio_backtest_v1.0.md`에
상세 설계만 있고 코드가 전혀 없다. PAPER→LIVE 승격은 execution_service의 항상-승인-필요
규칙과 core/approval의 SOLO/DUAL 서명으로 실동작하지만, 배포 상태기계는 실제 실행 루프가
쓰는 `strategy_executions`와, 정교하지만 `package_ref`가 불투명 문자열이라 전략과 연결되지
않는 `foundation/paper_control` 상태기계가 서로 배선되지 않은 채 이중으로 존재한다. mandate
(리스크 위임) 컨텍스트도 core 포트폴리오 엔진이 아직 참조하지 않는다. 마켓플레이스는 리스팅/
구매/수동검증/분쟁/지갑까지 실동작하지만 리스팅 메타데이터가 얇아(가격·상태뿐) provenance·
검증리포트 해시·risk envelope는 노출되지 않으며, 구매 확정이 곧바로 실행권한(FSM 정의 열람)과
동일시된다. 흥미롭게도 `src/contracts/enterprise.py`+`paper_strategy_projection.py`에
QuantDinger/AgenticTrading급의 완결된 StrategyPackage 계약(content_hash, risk_envelope,
hypothesis, validation_refs, license_ref)이 이미 존재하지만 어떤 API/DB에도 연결되지 않은
죽은 코드다. Performance Statement(`foundation/performance`)는 설계문서가 "미구현"이라 적은
것과 달리 이미 코드가 존재하지만, 스코프가 계정(tenant) 단위일 뿐 특정 전략 버전에 귀속되지
않는다. 전반적으로 설계 문서(L4 spec)가 스스로 인정한 감사 인용이 실제 코드 상태와 거의
정확히 일치하며, 이는 문서 자체가 최신 자기감사 성격을 갖고 있음을 시사한다.
