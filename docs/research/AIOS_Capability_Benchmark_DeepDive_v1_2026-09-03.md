# AIOS Capability ↔ Open-Source Implementation Benchmark

Deep Dive v1 — QuantDinger 중심 구현 비교 | 2026-09-03

> 원본: `brainstorm/AIOS_Capability_Benchmark_DeepDive_v1_2026-09-03.docx` (동일 내용, Markdown으로 이관).
> 9절에 기록된 대로 당시 GitHub connector로 이 저장소에 직접 커밋하지 못해 로컬 docx로만 존재했으며,
> 이번에 `docs/research/`로 이관·커밋했다.

## 1. 이번 Deep Dive의 범위

1차 광역 스캔의 S/A급 후보 가운데 공개 구현 문서가 충분한 QuantDinger, LEAN, AgenticTrading, OBaI를
중심으로 AIOS capability와 실제 구현 패턴을 대조했다. 추측성 기능은 제외하고 공개 README/architecture/API
문서에서 확인되는 것만 기록했다.

## 2. 핵심 판단

QuantDinger는 AIOS의 최종 목표 전체를 대체하는 프로젝트라기보다, 현재 공개 구현 중 AIOS의 Trading OS
계층과 가장 넓게 겹치는 비교 기준이다. 특히 Human API와 Agent Gateway 분리, scoped agent token,
idempotency, immutable strategy versions, paper/live gate, long-running trading worker 분리, durable
PostgreSQL state, MCP thin wrapper는 AIOS 설계에서 직접 비교할 가치가 높다.

반대로 AIOS가 목표로 하는 전략 자산의 표준 패키징·검증 증명·전략 마켓 거래·다중 AI provider를 통한 사용자
전략 생성·AIOS/DevEngine의 물리적 통제 경계는 여러 프로젝트의 패턴을 합쳐야 한다. 하나의 repo를 복제하는
방식으로는 목표에 도달하지 않는다.

## 3. Capability Map

| AIOS Capability | 확인된 비교 구현 | 공개 구현 패턴 | AIOS 적용 판단 |
|---|---|---|---|
| External AI ingress | Agent Gateway + MCP | Agent API를 Human API와 분리. MCP는 REST gateway의 thin wrapper. | 채택 후보: AIOS Agent Gateway를 별도 trust boundary로 유지. |
| Authorization | Scoped agent tokens | R/W/B/N/T/C scope, allowlist, rate limit, expiry, paper-only. | 강화 채택: capability token + tenant + strategy + instrument + notional + time-window policy. |
| Idempotency | Mutating agent requests | 모든 W/B/N/T mutation에 Idempotency-Key 요구, replay response 저장. | 강하게 채택. Event Ledger의 command_id와 통합. |
| Strategy lifecycle | Strategy API V2 | template→compile→save→immutable versions→deployment. | 채택하되 AIOS Strategy Package/IR 계층을 위에 추가. |
| Runtime separation | API / trading / scheduler / Celery | HTTP가 장기 루프를 소유하지 않고 별도 worker로 분리. | 채택. AIOS는 execution worker를 더욱 격리. |
| Durability | PostgreSQL durable state | commands, leases, orders, heartbeats 등을 durable DB에 저장. | 채택. Redis를 authoritative ledger로 사용하지 않음. |
| Async work | Celery + separate job Redis | 유한 retryable job과 장기 trading runtime을 분리. | 패턴 채택. queue 기술 자체는 교체 가능. |
| Market data | Adapter normalization | provider-specific columns를 adapter 안에 제한하고 normalized schema 반환. | 채택. AIOS canonical market-data contract로 확대. |
| Live trading gate | Multi-condition authorization | token scope + paper_only false + server flag + operator limits/allowlists. | 채택+강화. AI agent가 live permission 자체를 수정할 수 없게 함. |
| Marketplace | AgenticTrading Agent Marketplace | agent template catalog, author/repo, tags, model, strategy type. | 부분 참고. AIOS는 실행가능 전략의 provenance/signature/validation proof 필요. |
| Backtest | AgenticTrading / QuantDinger / LEAN | agent/strategy backtest 및 결과 추적. | AIOS는 OOS/walk-forward/leakage/fee/slippage validation gate를 별도 정책으로 강제. |
| Execution engine | LEAN | 장기간 운영된 algorithmic engine과 brokerage abstraction. | 직접 비교 기준. AIOS execution semantics의 성숙도 기준점. |
| Agent strategy generation | OBaI / AgenticTrading | AI/agent가 투자 로직 생성·실행/백테스트하는 흐름. | AIOS에서는 generated artifact가 바로 live execution으로 갈 수 없게 promotion gate 필요. |
| DevEngine | 별도 연구군 | Trading OS repo와 autonomous development orchestrator는 목적이 다름. | AIOS runtime과 DevEngine control plane을 물리·논리적으로 분리. |

## 4. QuantDinger에서 반드시 뜯어볼 구현

### 4.1 Human API / Agent API 이중 Surface

Human Web API는 JWT, Agent Gateway는 별도 agent token을 사용한다. 이 구조는 사용자 세션 권한과
autonomous agent 권한을 분리하는 핵심 패턴이다. AIOS에서도 외부 OpenAI/Anthropic/Gemini agent가 사용자
계정의 전체 권한을 상속해서는 안 된다.

### 4.2 Agent Token과 mutation idempotency

Agent token은 scope·allowlist·rate limit·expiry·paper-only 제약을 갖고, mutation은 Idempotency-Key를
요구한다. AIOS에서는 이를 Policy-as-Code와 Event Ledger에 결합해 모든 money-adjacent command를 재현
가능하게 해야 한다.

### 4.3 Strategy API V2

전략 소스가 compile되고 private source로 저장되며 immutable snapshot/version을 거쳐 stopped deployment로
만들어진다. AIOS는 이 앞단에 Strategy IR/Manifest를 추가해 LLM이 만든 전략과 사람이 만든 전략을 동일한
canonical artifact로 변환하는 것이 적합하다.

### 4.4 Worker ownership

API, trading worker, scheduler worker, Celery worker/beat를 분리한다. 장기 전략 runtime과 finite async
job을 같은 worker에 넣지 않는 원칙은 AIOS에도 그대로 유효하다.

### 4.5 MCP는 SSOT가 아님

MCP server가 Agent Gateway REST API를 감싸는 thin wrapper이고 REST가 source of truth다. AIOS도 MCP
자체에 business logic을 넣지 말고 capability protocol adapter로 제한하는 편이 안전하다.

### 4.6 Production hardening

non-root, read-only root filesystem, dropped capabilities, resource limits, API compatibility/security/secret
CI 등의 패턴이 확인된다. AIOS의 immutable security boundary와 결합할 수 있다.

## 5. QuantDinger를 그대로 따라가면 부족한 지점

- AIOS Strategy Marketplace는 단순 template catalog보다 강해야 한다. package signature, author identity,
  immutable version, dependency SBOM, validation report hash, supported markets/brokers, risk envelope,
  required data capability, provenance를 표준 필드로 가져야 한다.
- Strategy 생성 AI와 Strategy 승인/검증 주체를 분리해야 한다. 생성 모델이 자신의 validation threshold나
  live promotion policy를 변경할 수 없어야 한다.
- Backtest 결과 하나로 live 승격하지 않는다. 데이터 누수, look-ahead, survivorship bias, transaction cost,
  slippage, OOS, walk-forward, parameter sensitivity를 독립 gate로 둔다.
- 외부 사용자 AI는 AIOS 내부 구조를 탐색할 필요가 없다. Capability Gateway가 허용된 schema와 tool만
  노출하고 내부 DB·broker credential·policy implementation·DevEngine surface는 비공개로 유지한다.
- DevEngine은 AIOS의 금융 권한을 상속하지 않는다. 코드 변경 권한과 금융 실행 권한은 별도의 root of
  trust를 가져야 한다.

## 6. 제안 Target Architecture Delta

기존 AIOS 방향을 유지하면서 다음 계층을 명시적으로 고정하는 것이 좋다.

- **Experience Plane**: Web/Mobile/API + 사용자 AI 대화형 Strategy Builder.
- **Agent Gateway Plane**: OpenAI/Anthropic/Gemini/사용자 MCP client를 위한 최소 capability surface.
- **Strategy Factory**: NL intent → canonical Strategy IR → deterministic compiler → executable artifact.
- **Strategy Registry**: immutable versions, provenance, signature, dependency/SBOM, validation evidence.
- **Validation Plane**: backtest/OOS/walk-forward/leakage/robustness/risk gates. 생성 AI가 수정 불가.
- **Execution Plane**: broker/exchange adapters, order state machine, reconciliation, positions, kill switch.
- **Policy Plane**: 금융 행위 정책. Meta-Control boundary 아래 immutable policy roots.
- **Event Ledger**: command/event/audit/replay SSOT. Idempotency와 causation/correlation ID 통합.
- **Marketplace Plane**: 검증된 Strategy Package만 게시 가능. 구매/구독과 실행 권한을 분리.
- **DevEngine Plane**: AIOS 코드 유지보수용 별도 trust domain. PR/test/review를 거쳐 배포하며 금융
  credential 직접 접근 금지.

## 7. 다음 소스 분석 작업 큐

| Priority | Repository | 파일/구현 분석 목표 |
|---|---|---|
| P0 | QuantDinger | AGENT_ENVIRONMENT_DESIGN, AI_INTEGRATION_DESIGN, agent-openapi, Strategy API V2, live trading adapters, worker leases/reconciliation 실제 코드 |
| P0 | LEAN | order lifecycle, brokerage interface, transaction handler, algorithm framework, backtest/live parity |
| P0 | AgenticTrading | agent schema, marketplace storage/metadata, backtest execution, external agent API, credentials boundary |
| P0 | OBaI | strategy JSON/schema, iterative backtest loop, OOS validation, MCP exposure |
| P1 | Freqtrade | exchange adapter, strategy interface, dry-run/live switch, hyperopt, persistence/recovery |
| P1 | trade-terminal / AutoQuant | anti-overfitting guards, AST/sandbox, experiment ledger |
| P1 | PanCode / Archon / AutoCodeAI / MCO | DevEngine worker isolation, orchestration, reviewer independence, multi-provider routing |

## 8. 검증된 공개 근거

- QuantDinger architecture: https://github.com/OpenByteInc/QuantDinger/blob/main/docs/architecture/ARCHITECTURE.md
- QuantDinger Agent Gateway quickstart: https://github.com/OpenByteInc/QuantDinger/blob/main/docs/agent/AGENT_QUICKSTART.md
- QuantDinger AI integration design: https://github.com/OpenByteInc/QuantDinger/blob/main/docs/agent/AI_INTEGRATION_DESIGN.md
- QuantDinger MCP setup: https://github.com/OpenByteInc/QuantDinger/blob/main/docs/agent/MCP_SETUP.md
- AgenticTrading marketplace: https://github.com/Open-Finance-Lab/AgenticTrading/blob/main/docs/source/lab/marketplace.rst
- AgenticTrading getting started: https://github.com/Open-Finance-Lab/AgenticTrading/blob/main/docs/source/lab/getting_started.rst
- QuantConnect LEAN: https://github.com/QuantConnect/Lean
- OBaI: https://github.com/sixteen-dev/obai

## 9. GitHub 저장 상태 (작성 당시 기록, 원문 그대로 보존)

연결된 GitHub 계정에서 GeonAhGim 소유 repository 목록을 조회했으나 당시 connector가 반환한 목록은
0개였고, GeonAhGim/AIOSproject 직접 조회도 404였다. 따라서 이 문서는 로컬 산출물로 생성했으며
AIOSproject에는 임의로 저장했다고 주장하지 않는다. (이관 시점 후기: 실제 저장소는 `GeonAhGim/aios`이며,
본 이관으로 `docs/research/`에 커밋되었다.)
