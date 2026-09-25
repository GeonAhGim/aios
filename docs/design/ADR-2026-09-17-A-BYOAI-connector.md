# ADR-2026-09-17-A: BYOAI 에이전트 커넥터 — capability scope·Proposal 구조·보안 검토

## Status
Proposed (2026-09-17, CTO 검토 중). ideabank 2026-09-10/02에서 제안한 방향을 ADR-2026-09-05-A Agent Gateway 게이트로 구체화.

## Context

### 1. 외부 AI 에이전트의 AIOS 연결 요청
ideabank 02는 사용자가 자체 API 키/엔드포인트(Claude, Codex, Gemini, Ollama, vLLM 등)를 AIOS에 연결해 AIOS의 금융 데이터·전략 파이프라인을 외부 AI가 활용할 수 있는 방향을 제시한다. 이를 "Bring Your Own AI"(BYOAI)라고 명명한다.

### 2. Agent Gateway의 기존 설계
ADR-2026-09-05-A는 이미 Agent Gateway를 정의하고 있다:
- Gateway가 `read`, `research`, `script.create`, `backtest.run` 등 capability 단위로 권한을 분할
- 각 capability는 ADR-2026-09-05-A Decision A에서 정의한 capability scope를 따름
- 외부 AI가 직접 `src/`를 쓰지 못하도록 DSL 컴파일러·리소스 상한·no-lookahead 검증 파이프라인을 거치도록 설계

### 3. ideabank 02의 구체적 요구
- capability scope: `read`/`research`/`propose`/`paper`만 허용, `LIVE`/`출금`/`mandate 변경`/`kill 해제` 배제
- 외부 AI가 임의 Python 코드를 배포하지 않고 `StrategyProposal` 구조체로 제출
- 자격증명(credential)은 암호화 저장 또는 장기 보관 배제, tenant 분리, rotation, revoke, per-provider budget, per-agent scope
- prompt injection이 실행 권한으로 연결되지 않도록 capability isolation
- PAPER 실행 전 사용자 승인 UI + one-time ticket/action digest로 preview-execution binding

### 4. U-3a(에이전트 게이트웨이)와의 관계
L4_product_experience_and_discovery_v1.0의 U-3는 "사용자가 자연어로 요청하면 AIOS Script 초안 생성·백테스트 해설·요약 제공"이며, 구현 주체는 에이전트 게이트웨이 + DSL 컴파일러 + BT + RD-7 리서치 조회이다.

BYOAI 커넥터는 U-3a의 **구현 확장**이다:
- U-3a가 기본 내장 AI(AGI-01)를 전제로 할 때, BYOAI는 외부 AI를 게이트웨이에 연결하는 **adapter 계층**
- capability 라우팅·credential 관리·Proposal 검증 파이프라인은 U-3a의 기존 게이트웨이에 통합
- U-3a의 DSL 컴파일·리소스 상한·no-lookahead 검증은 BYOAI 경로도 동일하게 거침

## Decision

### D1: capability scope — 읽기/연구/제출/페이퍼만 허용

| 허용 capability | 의미 | 게이트웨이 배선 |
|---|---|---|
| `read` | 시세·종목·포트폴리오·백테스트 결과 읽기 | `GET /api/v1/market/*`, `GET /api/v1/portfolio/*` |
| `research` | 공시·뉴스·리서치 데이터 읽기 | `GET /api/v1/research/*`, `GET /api/v1/open-dart/*` |
| `propose` | `StrategyProposal` 제출 | `POST /api/v1/strategy-proposals` |
| `paper-request` | PAPER 실행 요청(승인 대기) | `POST /api/v1/experiments/paper-request` |

| 거부 capability | 의미 | 처리 |
|---|---|---|
| `live-trade` | LIVE 주문 실행 | Gateway가 403 반환, 커넥터가 직접 호출 시도해도 차단 |
| `withdraw` | 자금 출금 | Gateway가 403 반환 |
| `mandate-modify` | mandaate 조건 변경 | Gateway가 403 반환 |
| `kill-unlock` | Kill Switch 해제 | Gateway가 403 반환 |
| `write-src` | 저장소 파일 쓰기 | 커넥터가 `src/` 경로를 마운트하지 않음 |

**결정:** capability scope는 위 표를 고정값으로 게이트웨이에서 강제. 커넥터가 capability를 요청할 때 Gateway가 whitelist로 검증한다.

### D2: StrategyProposal 구조 — 임의 코드 배포 배제

외부 AI가 제출하는 전략은 반드시 다음 JSON 스키마를 따른다:

```text
StrategyProposal {
  script_source: string       // AIOS Script DSL (Python 아님)
  hypothesis: string          // 자연어 가설
  data_scope: DataScope       // 사용할 데이터 범위
  params: map<string, decimal> // 파라미터
  provider_ref: ProviderRef   // 연결한 외부 AI provider 참조
  metadata: PromptVersionMeta // 사용한 prompt/version 정보
}

DataScope {
  universe: string            // 종목 범위 (예: "KOSPI200")
  start: UTC datetime
  end: UTC datetime
  timeframe: string           // "1m", "1d" 등
}

ProviderRef {
  provider_id: string         // 연결한 커넥터 ID
  model: string               // 사용 모델
  prompt_version: string
}
```

**결정:** `script_source`는 DSL 컴파일러가 검증한다. Python 코드는 받지 않는다. DSL이 불가능한 경우에만 `NotImplementedError` + `ratchet-allow`로 처리 (ADR-2026-09-05-A 규칙).

### D3: credential 관리 — 장기 보관 배제

| 항목 | 정책 |
|---|---|
| 저장 형식 | 암호화 저장 (AES-256-GCM), 또는 OAuth authorization code flow로 AIOS가 token을 장기 보관하지 않음 |
| tenant 분리 | 각 tenant의 credential은 키 분리 암호화 |
| rotation | provider별 rotation 주기 강제 (최대 90일) |
| revoke | 사용자 또는 provider가 revoke 시 Gateway가 즉시 capability 무효화 |
| per-provider budget | provider별 월간 토큰/요청 상한, 초과 시 명시적 오류 |
| per-agent scope | 각 agent(커넥터 인스턴스)별로 capability scope 분리 |

**결정:** credential은 `src/foundation/connections/adapters/` 하위에 credential-specific adapter로 구현. 공통 credential 인터페이스는 `src/foundation/connections/domain/credential_store.py`에 배치.

### D4: prompt injection 방어 — capability isolation

| 공격 경로 | 방어 |
|---|---|
| 외부 AI prompt → AIOS Script injection | `script_source`는 DSL 컴파일러가 파싱. 자연어 텍스트가 DSL 토큰으로 해석되지 않도록 lexer가 엄격 모드 |
| 외부 AI가 다른 tenant의 credential 읽기 | Gateway가 tenant ID를 모든 쿼리에 주입, credential store가 tenant 키로 분리 |
| 외부 AI가 prompt로 다른 capability 요청 | Gateway가 capability를 라우팅 전 재검증 (defense in depth) |
| 외부 AI가 backtest 결과로 정보 리플로우 | `read` capability의 응답은 데이터 스칼라만 반환, 원본 prompt/시스템 메시지는 노출 안 함 |

**결정:** prompt injection 방어는 application 계층에서 처리한다. 외부 AI의 prompt 자체는 AIOS가 저장하지 않으므로, injection은 오직 `StrategyProposal.script_source` 경로를 통해서만 가능하며 DSL lexer가 막는다.

### D5: PAPER 실행 승인 — one-time ticket

사용자가 "PAPER에서 돌려"라고 요청할 때:
1. Gateway가 `StrategyProposal`을 검증
2. 사용자 승인 UI 표시 (최대 운용금, 예상 MDD, Risk Gate 결과)
3. 확인 시 one-time ticket 발급 (JWT 기반, 5분 유효)
4. PAPER 실행 요청에 ticket 검증 → 같은 요청임을 보장
5. 실행 결과는 experiment ledger에 기록

**결정:** one-time ticket은 action digest 방식. ticket payload에 proposal hash를 포함해 preview와 execution이 같은 요청임을 검증한다.

### D6: 커넥터 아키텍처 — adapter 패턴

```
BYOAI Connector (adapter)
├── credential_manager.py   # 암호화/rotation/revoke
├── api_client.py           # provider별 API 호출 (HTTP/SSE)
├── capability_router.py    # Gateway capability whitelist 검증
├── proposal_builder.py     # 외부 AI 응답 → StrategyProposal 변환
└── injection_guard.py      # DSL lexer 검증 보조
```

각 외부 AI provider(Claude, Codex, Gemini 등)는 별도의 adapter 파일로 구현. 공통 인터페이스는 `src/foundation/connections/domain/agent_connector.py`에 정의.

## Rejected

### R1: 외부 AI에게 직접 `src/` 마운트 허용
- 이유: prompt injection으로 코드 삽입 시 저장소 파일 직접 수정 가능. ADR-2026-09-05-A의 "외부 AI가 직접 `src/`를 쓰지 못한다" 원칙에 위배.
- 대안: `StrategyProposal` → DSL 컴파일 → 검증 파이프라인을 통하도록 한다.

### R2: 외부 AI에게 LIVE 권한 부여
- 이유: ADR-2026-09-05-A Decision B에서 LIVE는 "AIOS가 최종 승인"하도록 명시. AI는 제안자이지 Master Authority가 아님.
- 대안: `propose` → 검증 → user mandate → user approval → AIOS risk/compliance → controlled execution 파이프라인.

### R3: 외부 AI의 credential을 plain text로.env에 저장
- 이유: tenant 분리, rotation, revoke가 불가능. ADR-2026-09-10-A의 암호화 저장 원칙에 위배.
- 대안: D3에서 정의한 암호화 저장 + rotation.

### R4: MCP로 외부 AI 연결
- 이유: MCP는 도구 스키마가 컨텍스트를 차지하며, ADR-2026-09-10-B에서 MCP는 최대 3개/레인, 효과/비용 순서로 도입하도록 명시. BYOAI 커넥터는 HTTP API/SSE 기반이 적합.
- 대안: HTTP API 기반 커넥터. 향후 MCP 지원은 별도 ADR에서 검토.

## 보안 검토 체크리스트

| # | 항목 | 상태 | 비고 |
|---|---|---|---|
| 1 | credential 암호화 저장 (AES-256-GCM) | 필수 | `credential_store.py` 구현 시 적용 |
| 2 | tenant 키 분리 | 필수 | 각 tenant별 encryption key |
| 3 | credential rotation 강제 (90일) | 필수 | background job 또는 lazy check |
| 4 | revoke 즉시 반영 | 필수 | Gateway capability cache 무효화 |
| 5 | per-provider budget 상한 | 필수 | 초과 시 429 + 명시적 오류 |
| 6 | per-agent capability scope | 필수 | Gateway whitelist로 강제 |
| 7 | prompt injection — DSL lexer 엄격 모드 | 필수 | natural text → DSL token 해석 방지 |
| 8 | one-time ticket으로 preview-execution binding | 필수 | proposal hash 포함 JWT, 5분 유효 |
| 9 | 외부 AI 응답의 data scope 제한 | 필수 | `read` capability는 스칼라만 반환 |
| 10 | Gateway capability 재검증 (defense in depth) | 필수 | adapter → Gateway → router 3단계 검증 |

## 영향 받는 리프

| 리프 | 영향 |
|---|---|
| L4_ai_research_strategy_factory_v1.0 (AGI-01~03) | AGI-02의 external AI integration에 BYOAI 커넥터 배선 |
| L4_product_experience_and_discovery_v1.0 (U-3a) | 에이전트 게이트웨이에 BYOAI capability 라우팅 통합 |
| L4_product_experience_and_discovery_v1.0 (U-10) | AI Connections 화면에 BYOAI 커넥터 상태 표시 |
| L4_copilot_and_review_v1.0 (C-01~03) | C-02의 external AI review에 BYOAI connector 활용 가능 |

## 관련 ADR

- ADR-2026-09-05-A: Agent Gateway capability scope 정의
- ADR-2026-09-08-B: Copilot/Coding Agent Lane — 외부 에이전트 처리 패턴
- ADR-2026-09-10-A: Context-aware Review — credential/보안 관련 MCP 배선
- ADR-2026-09-10-B: 워커 효율 커넥터 — MCP 도입 원칙 (BYOAI는 MCP 아님)

## Consequences

- 구현: CONN-1(byoai connector domain), CONN-2(credential store), CONN-3(gateway capability router 확장), CONN-4(proposal builder + injection guard)
- 보안 검토 체크리스트 10항 모두 구현 시 D2 증빙으로 기록
- U-3a 게이트웨이에 BYOAI capability 라우팅 통합 시 U-3a D2 증빙에 추가
- 장기 포지션: "AI가 포함된 자동매매 서비스" → "어떤 AI든 안전하게 연결할 수 있는 금융 운영체제"
