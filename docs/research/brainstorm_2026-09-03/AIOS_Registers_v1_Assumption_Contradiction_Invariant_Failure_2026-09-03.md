# AIOS 연구 레지스트리 v1 — Assumption · Contradiction · Invariant · Failure Scenario

작성: Fable | 2026-09-03 | [연구 결정 기록](AIOS_Codex_Fable_CrossReview_Research_Direction_Record_2026-09-03.md) §7
"연구 루프"가 요구하는 4종 레지스트리. 근거는 [`AIOS_OSS_DeepDive_v2`](AIOS_OSS_DeepDive_v2_CodeLevel_CrossVerification_2026-09-03.md)와
[`research_evidence_2026-09-03/`](research_evidence_2026-09-03/)의 file:line 인용을 그대로 재사용한다 — 이 문서는
새 조사를 하지 않고 기존 증거를 4개 표준 형식으로 재구성한다.

> 표기: 각 항목 ID는 `A-nn`(Assumption), `C-nn`(Contradiction), `I-nn`(Invariant), `F-nn`(Failure
> Scenario). Architecture Synthesis(다음 문서)는 이 ID를 인용해 결정 근거를 남긴다.

---

## 1. Assumption Register — 검증되지 않은 채 설계에 깔린 전제

| ID | 가정 | 어디에 깔려 있나 | 검증 상태 | 깨지면 무슨 일이 일어나는가 |
|---|---|---|---|---|
| A-01 | PostgreSQL 하나가 유일한 권위 상태(single source of truth)이며 항상 가용하다 | 전체 아키텍처(리스, 커맨드, 이벤트, 감사증적 전부 PG 트랜잭션에 의존) | **미검증** — PG 자체의 HA/failover 전략이 어떤 설계 문서에도 없음(`ext_quantdinger.md` §1, §8 시사점) | PG가 죽으면 리스 갱신도, kill switch 상태 조회도, 감사 기록도 동시에 멈춘다 — 단일 장애점 |
| A-02 | 하나의 HTTP 프로세스만 실행 루프를 돈다 | `background_loops.py`의 모든 스케줄링 로직, `scheduler.list_runnable()`이 소유권 필터 없음 | **거짓으로 확인됨** — 배포 스크립트나 오토스케일링이 인스턴스를 2개 이상 띄우는 순간 이 가정이 깨진다(`aios_runtime_execution.md` §1) | P0-R1의 근거 자체. 중복 tick·중복 주문 |
| A-03 | "코드/ADR 변경 없이는 LIVE로 못 뒤집는다"는 강한 정적 게이트가, 나중에 실제 기관 고객에게 필요한 운영 유연성(제한적 LIVE 파일럿, 계정별 점진 롤아웃)과 충돌하지 않는다 | ADR-2026-08-29-E, `Executor.execute()` | **미검증** — QuantDinger/AgenticTrading은 반대로 런타임 토글+토큰스코프 방식을 택했다(`aios_auth_gateway.md` §5) | 나중에 "일부 기관 고객에게만 제한적 LIVE 허용" 같은 요구가 오면 현재 설계는 코드 재작성 없이는 대응 불가 |
| A-04 | 조건식이 AND/OR 평면 문자열만으로 충분하다("복잡한 전략은 나중에") | `condition_compiler.py`, "조건트리 v2"가 설계만 있고 미구현으로 방치된 지 여러 사이클 경과(`aios_strategy_lifecycle.md` §1) | **점점 더 의심스러움** — OBaI의 `StrategyDefinition`이 이미 AND/OR를 넘어서는 구조를 실사용 중(`ext_agentictrading_obai.md` Part B §1) | AIOS가 이 갭을 방치하는 동안 경쟁 OSS의 표현력이 이미 앞서 있다 |
| A-05 | 마켓플레이스 신뢰는 "수동 검증(사람이 체크리스트 확인)"으로 충분하다 | `VerificationService.decide` | **위험한 가정** — 검증이 `strategy_validation_result`를 아예 참조하지 않는다(`aios_strategy_lifecycle.md` §5). AgenticTrading은 이 가정으로 실패했다("No whitelist, on purpose"라고 코드가 자인) | 자동 백테스트 결과와 무관하게 사람이 승인하면 리스팅된다 — provenance 사슬이 끊긴 채 판매 가능 |
| A-06 | 에이전트/외부 AI에게 열어줄 API 표면이 필요해지면 그때 가서 설계해도 늦지 않다 | 현재 MCP/에이전트 게이트웨이 코드 0건(`aios_auth_gateway.md` §1) | **1차 Deep Dive의 전제 자체와 모순** — AIOS의 최초 리서치 동기가 "다중 AI provider를 통한 사용자 전략 생성"이었다(`docs/research/AIOS_Capability_Benchmark_DeepDive_v1...md` §2) | 목표 기능(AI 에이전트 연동)이 가장 늦게 설계되는 역설 |
| A-07 | 백테스트와 실행 루프는 별도 엔진이어도 결과가 충분히 일치한다 | `foundation/backtest`와 `execution_loop`가 완전히 분리된 코드 | **거짓 가능성 높음** — LEAN/QuantDinger/Freqtrade 셋 다 "같은 도메인 코드 공유"를 패리티의 필수조건으로 삼음(`AIOS_OSS_DeepDive_v2` §4.7) | 백테스트를 통과한 전략이 실전에서 다르게 행동해도 원인을 코드로 추적할 수 없다 |
| A-08 | Redis 없이 asyncio.Queue 기반 인메모리 이벤트 버스로 충분하다(현재 규모에서는) | `src/core/event_bus/in_process.py` | **규모가 커지면 재검토 필요** — QuantDinger조차 캐시용/큐용 Redis를 분리해서 쓴다(`ext_quantdinger.md` §1) | 프로세스가 여러 개가 되는 순간(A-02가 깨지는 순간) 이 가정도 함께 깨진다 — A-02와 강결합 |

---

## 2. Contradiction Register — 서로 다른 답을 내리고 있는 설계

| ID | 모순 | 증거 A | 증거 B | 해소가 필요한 이유 |
|---|---|---|---|---|
| C-01 | **배포 상태기계가 두 개 존재하고 서로 모른다** | `strategy_executions`(실제 실행 틱이 사용, `mode/status/PENDING_APPROVAL→RUNNING→PAUSED→RETIRED`) | `foundation/paper_control`(fence_token·mandate_revision_id를 갖춘 더 정교한 `REQUESTED→READY→RUNNING→...` 상태기계, `package_ref`가 불투명 문자열이라 전략과 연결 안 됨) | `tick.py`가 후자를 전혀 참조하지 않는다(`aios_strategy_lifecycle.md` §4). 두 상태기계 중 하나가 죽어야 하는데 지금은 둘 다 살아서 서로 다른 진실을 말할 수 있다 |
| C-02 | **멱등성 구현이 두 벌이다** | `src/core/idempotency.py`(범용 claim-first, 실사용 1곳) | `foundation/paper_control`의 PAP-006(요청 다이제스트 대조, FAILED도 재현) | 어느 쪽이 표준인지 정하지 않으면 세 번째 구현이 또 생긴다(`aios_auth_gateway.md` §3) |
| C-03 | **리스크 한도가 두 레이어에서 서로 다른 단위로 표현된다** | `foundation/mandates`(비율 기반: `max_total_exposure_pct` 등) | `core/risk/engine.py` + `config/risk_policy.yaml`(절대 지표 8종: VaR, 레버리지 등) | `core/portfolio/engine.py`가 mandate를 전혀 참조하지 않는다는 것이 L4 스펙 자체의 지적(`aios_strategy_lifecycle.md` §4). 두 레이어가 상호 검증되지 않는 채로 각자 "이 정도면 안전하다"고 판단할 수 있다 |
| C-04 | **Live 게이트 철학이 반대 방향이다** | AIOS: 코드/ADR 레벨 정적 하드 블록, 런타임 토글 없음 | QuantDinger/AgenticTrading: 토큰 스코프 + paper_only 플래그 + 서버 kill switch + notional 예약의 **런타임** 다단 게이트 | A-03과 동일 뿌리. AIOS가 "더 보수적"이라고 자평할 근거는 있지만, 운영 유연성을 포기한 것이 의도적 선택인지 단순 미구현인지 문서에 없다 |
| C-05 | **"게이트가 있다"의 의미가 프로젝트 내에서 다르게 쓰인다** | `foundation/risk_gate`, `make_foundation_pre_submit_gate()` — 코드는 완성, 배선은 0건 | `core/risk/engine.py` — 코드도 완성, 배선도 완료(9지표가 실제로 매 틱 평가됨) | 같은 저장소 안에서 "구현됨"이 "작동함"을 의미하는 곳과 의미하지 않는 곳이 섞여 있다 — 이게 바로 결정 기록이 "IMPLEMENTED ≠ WIRED" 원칙을 세운 이유이자, 표준화가 필요한 지점 |
| C-06 | **MCP의 역할에 대한 두 개의 반대 사례가 공존한다** | QuantDinger: MCP는 REST의 얇은 프록시, 비즈니스 로직 0 | OBaI: MCP 서버 자체가 서비스, 비즈니스 로직이 MCP 안에 있음 | AIOS가 아직 MCP를 안 만들었으니 "모순"이라기보다 "선택되지 않은 갈림길" — Invariant I-08로 해소 |

---

## 3. Invariant Catalog — AIOS 전체에 강제해야 할 불변조건 (표준화의 실체)

이 표가 "기능 표준화"의 1차 산출물이다. 각 불변조건은 §1/§2의 근거에서 도출됐다.

| ID | 불변조건 | 근거 |
|---|---|---|
| I-01 | **주문 제출·승인 경로를 구성하는 어떤 서비스/스케줄러 생성자도 안전 게이트 인자를 `Optional`/기본값 `None`으로 받지 않는다.** 게이트는 필수 인자이며, 컴파일/구성 시점에 누락이 드러나야 한다. | P0-R2, P0-R3 — 두 결함 모두 "게이트 인자가 Optional이라 조용히 None으로 흘러들어갔다"는 동일 근본원인(`AIOS_OSS_DeepDive_v2` §7.2) |
| I-02 | **둘 이상의 프로세스/워커가 동시에 읽을 수 있는 모든 실행-소유권 상태는 명시적 lease/fencing token을 가지며, 소유자가 바뀔 때만 토큰이 증가한다.** 리스 갱신 실패 시 로컬에서 즉시 중지한다(계속 실행하는 쪽을 기본값으로 삼지 않는다). | P0-R1. 참조 구현 QuantDinger `qd_strategy_runtime_leases`(`ext_quantdinger.md` §8) |
| I-03 | **멱등키는 최소 (tenant, actor/token, route, content-hash) 4중으로 스코프되며, 단일 헤더값만으로 키를 구성하지 않는다.** 동일 키에 다른 payload가 오면 409로 거부한다. | C-02, `aios_auth_gateway.md` §3, 참조 QuantDinger `qd_agent_idempotency`(`ext_quantdinger.md` §3) |
| I-04 | **전략 아티팩트는 버전이 부여되는 순간 콘텐츠 해시로 주소화되고 불변이 된다.** 불변성은 애플리케이션 관례가 아니라 DB 쓰기 권한 제거 또는 트리거로 강제한다. | `aios_strategy_lifecycle.md` §1, 참조 QuantDinger `qd_script_source_versions`(단 QuantDinger도 이걸 관례로만 강제해 자체 한계로 지적함, `ext_quantdinger.md` §5) |
| I-05 | **백테스트와 라이브 실행은 동일한 컴파일된 전략 프로그램과 동일한 도메인 로직(주문/포지션/비용 모델)을 공유한다.** 두 경로가 별도 엔진이면 그 자체가 결함이다. | A-07, 참조 LEAN `IBrokerageModel`/QuantDinger 단일 컴파일 산출물/Freqtrade `LocalTrade` vs `Trade`(`AIOS_OSS_DeepDive_v2` §4.7) |
| I-06 | **AI/외부 에이전트에게 노출되는 모든 capability는 인간 세션 인증과 물리적으로 분리된, 스코프가 닫힌 enum으로 제한된, 서버측에서 즉시 revoke 가능한 토큰을 통해서만 접근된다.** 에이전트는 자신의 권한을 발급·확장·조회하는 API 표면에 도달할 수 없다. | A-06, C-06, 참조 QuantDinger `agent_required` vs `login_required` 물리적 분리(`ext_quantdinger.md` §2, §4) |
| I-07 | **검증/승인 게이트의 hard-fail 조건은 도메인 코드에서 계산되며, 실제로 FAIL을 반환할 수 있어야 한다.** 프롬프트나 정책 문서에만 존재하는 임계값은 게이트가 아니라 가이드라인이다. | §3.2 수렴 발견(hard_fail_reasons 항상 빈 튜플 ≒ OBaI 프롬프트 게이트), `AIOS_OSS_DeepDive_v2` §3.2 |
| I-08 | **MCP/도구 서버는 REST/도메인 계층이 이미 강제하는 것 이상의 인가·비즈니스 로직을 갖지 않는다.** MCP가 뚫려도 그 자체로 새로운 권한이 열리지 않아야 한다. | C-06, 참조 QuantDinger(썸원)와 OBaI(반례)의 대비(`ext_quantdinger.md` §6, `ext_agentictrading_obai.md` Part B §4) |
| I-09 | **리스크 한도는 단일 권위 레이어에서 평가된다.** 여러 레이어(mandate 비율, RiskEngine 절대 지표)가 존재해도 되지만, 최종 ALLOW/DENY는 하나의 합성 지점을 반드시 거치고, 그 지점이 하위 레이어들을 모두 조회했다는 것을 증거로 남긴다. | C-03, P0-R5(Authority Wiring Proof) |
| I-10 | **"구현됨"은 "작동함"을 함의하지 않는다.** 모든 안전/정책 컴포넌트는 실행 경로 배선 여부를 증명하는 자동화된 테스트(정적 검사 또는 적대적 통합 테스트)를 가져야 신규 기능으로 인정된다. | C-05, 연구 결정 기록 §10 "IMPLEMENTED → WIRED → NON-BYPASSABLE → EVIDENCE-PROVABLE" |
| I-11 | **[2026-09-03 추가] 확인(confirmation)이 필요한 작업은 1회성 서버측 토큰/상태로 미리보기와 실행을 연결한다.** 클라이언트가 반복 전송 가능한 플래그(`confirm=true`) 하나만으로 확인을 대신할 수 없다. | Phase 2 segnals-mcp 조사(`AIOS_OSS_DeepDive_v4` §"새로 확인된 것" 3번) — 미리보기 호출과 확인 호출 사이에 nonce가 없어 LLM이 첫 호출부터 `confirm=true`를 보내면 우회됨 |

---

## 4. Failure Scenario Catalog — 구체적 공격/장애 시나리오

| ID | 시나리오 | 트리거 조건 | 현재 결과 | 관련 불변조건 |
|---|---|---|---|---|
| F-01 | 블루/그린 배포 중 신·구 인스턴스가 동시에 같은 실행을 tick | 배포 스크립트가 잠깐이라도 인스턴스 2개를 동시에 띄움 | 같은 전략이 중복 주문을 낸다(`scheduler.list_runnable()`에 소유권 필터 없음) | I-02 (P0-R1) |
| F-02 | 운영자가 GLOBAL kill switch를 ACTIVE로 올림 | 사고 대응 중 | 실행 루프의 신규 주문 제출이 **막히지 않는다**(`pre_submit_gate=None`) | I-01 (P0-R2) |
| F-03 | 시세 조작/이상치가 3소스 쿼럼 편차를 만듦 | 데이터 프로바이더 장애 또는 조작 | DataDistrust 판정이 항상 NORMAL로 고정되어 있어 이상 시세를 걸러내지 못함(`distrust_monitor=None`) | I-01 (P0-R3) |
| F-04 | 과최적화된(overfit) 전략이 검증 파이프라인을 통과 | 어떤 입력을 넣어도 | `hard_fail_reasons`가 구조적으로 항상 빈 튜플이라 FAIL이 나올 수 없음 — PASS 또는 PASS_WITH_OBLIGATIONS만 가능 | I-07 |
| F-05 | 변조되거나 검증 안 된 전략이 마켓플레이스에 리스팅 | 판매자가 검증 요청 후 사람이 체크리스트만 보고 승인 | `strategy_validation_result`를 검증 절차가 아예 조회하지 않음. 죽어 있는 `enterprise.py` 서명 메커니즘이 이걸 막을 수 있었지만 배선 안 됨 | I-04, I-07 |
| F-06 | 두 사용자가 같은 Idempotency-Key 헤더값을 우연히/의도적으로 사용 | 헤더 재사용 | 마켓플레이스 외 대부분의 라우터는 표준화된 스코프 의존성이 없어 재발명된 각자의 스코프 규칙에 의존 — 취약한 라우터가 나올 위험 상존 | I-03 |
| F-07 | 에이전트(외부 AI)가 프롬프트 인젝션으로 자신의 권한 확장을 시도 | (가상 시나리오, AIOS엔 아직 에이전트 게이트웨이 자체가 없음) | 현재는 애초에 그런 API 표면이 없어 발생 불가하지만, 설계 없이 서둘러 만들면 QuantDinger가 막아둔 "자기 권한 상승 경로" 부재를 재현하지 못할 위험 | I-06 |
| F-08 | Redis(또는 유사 인프라)를 나중에 rate limiter로 도입했는데 장애 시 in-memory fallback으로 전환 | 인프라 장애 | (아직 AIOS엔 rate limiter 자체가 없음 — 그러나 QuantDinger의 실제 결함 사례: gunicorn 멀티워커에서 fallback이 프로세스별로 분리돼 실질 한도 초과 허용) 새로 만들 때 이 실수를 반복하지 않도록 사전 등록 | I-01의 확장 사례 |
| F-09 | 백테스트를 통과한 전략이 실거래에서 다르게 행동 | 백테스트 엔진과 실행 루프가 다른 fee/fill/slippage 가정을 씀 | 두 엔진이 코드를 공유하지 않아 괴리의 원인을 코드로 추적할 방법이 없음 | I-05 |
| F-10 | 재시작 후 "오늘 손실 얼마인지" 기준점이 리셋 (참고: 이미 수정된 과거 사례) | 프로세스 재시작 | **이미 수정됨** — `equity_tracker`가 DB write-through로 개선(`aios_runtime_execution.md` §6). 이 카탈로그에 남기는 이유: 같은 클래스의 실패(재시작 시 인메모리 기준점 유실)가 다른 컴포넌트(리스, DataDistrust 상태 등)에서 반복되지 않는지 매 신규 기능마다 체크리스트로 확인 | I-10 |
| F-11 | [2026-09-03 추가] LLM 에이전트가 "미리보기 → 확인" 2단계 도구를 첫 호출부터 `confirm=true`로 호출해 사람 확인 단계를 건너뜀 | Agent Gateway에 확인 게이트가 서버측 상태 없이 클라이언트 플래그로만 구현된 경우 | (아직 AIOS엔 Agent Gateway 자체가 없어 발생 불가 — Phase 2 segnals-mcp 조사에서 실제 발견된 결함을 새로 만들 때 반복하지 않도록 사전 등록) | I-11 |
| F-12 | [2026-09-03 추가] "검증 임계값 미달"이 계산은 되지만 저장/커밋/배포 경로를 실제로 막지 못함 | 검증 결과가 재시도 여부에만 쓰이고 저장 함수는 결과와 무관하게 무조건 실행되는 코드 | AIOS 자신(`hard_fail_reasons`) + OBaI + AgentQuant + coinjure + agenttrader + autoquant에서 **6개 독립 사례** 확인(`AIOS_OSS_DeepDive_v4` "가장 중요한 발견") — LLM 에이전트 판정 시스템 전반의 구조적 함정으로 격상 | I-07 |

---

## 5. 다음 단계

이 레지스트리를 근거로 [Target Architecture 동결 초안](AIOS_Target_Architecture_Freeze_v0.1_2026-09-03.md)을
작성한다. Contradiction(§2) 6건 전부가 그 문서에서 명시적으로 해소돼야 하고, Invariant(§3) 10건은
그 문서의 각 plane에 "이 plane이 어떤 invariant를 누가 강제하는가" 컬럼으로 매핑된다.
