# 외부 저장소 코드 레벨 분석 — trading-agent / LLM-Auto-Trader

조사일: 2026-09-03
방법: `git clone --depth 1` 후 `--unshallow`로 전체 히스토리 확보, 코드 직접 읽기, `gh api`로 GitHub 메타데이터(스타/포크/이슈) 확인. 실행/배포는 하지 않음 (read-only).

---

## Part A — parlali/trading-agent

### A.1 실체 (Marketing vs Reality)

GitHub 저장소 설명: *"TypeScript trading infrastructure for AI-assisted strategy execution, risk controls, broker reconciliation, and operational monitoring."*

README(`README.md:1-20`)의 자체 설명이 사실상 정확하다 — 과장이 거의 없다:

```
# Trading Runtime
Open-source trading infrastructure for running LLM-assisted strategies with
deterministic risk enforcement, provider-truth reconciliation, and a typed
Convex control plane.
...
## Safety Model
Agents propose intents. Deterministic code owns execution permission, risk
validation, account ownership, order lifecycle, accounting, kill switches,
provider identity checks, persistence, and reconciliation.
```

구조: Turborepo 모노레포. `apps/backend`(TypeScript 스케줄러/에이전트 런타임/헬스서버), `apps/dashboard`(Next.js 운영 대시보드, Convex 백엔드), `packages/core`(공유 타입·리스크 게이트), `packages/agent`(도구 계약·MCP 통합·모델 프로바이더), `packages/convex`(스키마/쿼리/뮤테이션), 그리고 venue별 패키지 `packages/{alpaca-options,mt5,okx,polymarket}`.

규모: TypeScript 파일 586개, `apps/backend/src`만 20,723줄, 테스트 파일 170개(전체 ts 파일의 29%). Bun + Vitest 기반(`package.json:14` `"test": "bunx vitest run"`).

### A.2 LLM ↔ 결정론적 실행의 코드 레벨 분리 검증

이것이 이 저장소의 핵심 주장이며, 실제로 **진짜 프로세스/모듈 경계가 존재한다.** LLM 도구 호출은 항상 `propose_*` 네이밍을 쓰고, 실제 주문 실행은 별도 파이프라인 함수를 거친다.

`packages/agent/src/tools/propose-order.ts` (LLM이 호출하는 도구):

```ts
export function createProposeOrderTool(
    pipeline: ExecutionPipeline
): ToolBinding {
    return createToolBinding({
        name: "propose_order",
        venue: "alpaca-options",
        handler: async (params, context) => {
            const validated = params as z.infer<typeof alpacaOrderParamsSchema>
            const intent: OrderIntent = { ... }
            return await executeToolIntent(pipeline, intent, { action: "entry" }, {
                includeTrackedOrder: true,
                signal: context?.signal,
            })
        },
    })
}
```

`packages/agent/src/tools/execution-response.ts:89-119`의 `executeToolIntent`가 실제 경계다 — LLM이 만든 `intent`를 그대로 실행하지 않고, `pipeline.executeIntent()`에 위임한다. 이 함수 내부는 별도 계층(`ExecutionPipeline`, 리스크 게이트, venue adapter)이며 LLM 프로세스와 분리된 결정론적 TypeScript 코드다:

```ts
export async function executeToolIntent(
    pipeline: ExecutionPipeline,
    intent: OrderIntent,
    lifecycleContext: OrderLifecycleContext,
    options: {...} = {}
): Promise<Record<string, unknown>> {
    const [positions, account] = await Promise.all([...])
    const { result, validation, handle } = await pipeline.executeIntent(
        intent, account, positions, lifecycleContext
    )
    return toExecutionToolResult(result, {
        trackedOrder: options.includeTrackedOrder ? handle?.snapshot : undefined,
        validation,
    })
}
```

주문이 거부되면 `platformHardBlock` 필드로 "risk_engine"이 명시적 이유를 붙여 반환한다(`execution-response.ts:75-80`). 이는 LLM에게 "그렇게 하지 말라"고 프롬프트로 부탁하는 게 아니라, LLM이 무엇을 요청하든 별도 TypeScript 함수가 통과/차단을 최종 결정하는 구조다. `mechanical-condor` 같은 규칙 기반 엔진(`README.md:181,214`)도 "same execution pipeline as agent tool calls"를 통과하도록 설계되어 있어, LLM 경로와 엔진 경로가 같은 강제 계층을 공유한다.

**결론: 실제 코드 경계 있음.** "AI proposes, system decides"는 마케팅 문구가 아니라 함수 시그니처 수준에서 확인된다.

### A.3 리스크 컨트롤 — 프롬프트가 아니라 코드

리스크 로직은 `packages/core/src/risk.ts`(공통 게이트 프리미티브)와 venue별 `*/src/risk-rules.ts` 4종(mt5, okx, polymarket, alpaca-options의 `equity-risk-rules.ts`)으로 나뉘어 있으며, 전부 순수 TypeScript 함수 + 테스트가 붙어 있다(`risk.test.ts`, `risk-rules.test.ts` 등 6개 테스트 파일 확인).

`packages/mt5/src/risk-rules.ts:33-45` — LLM이 stop-loss/take-profit 없이 주문을 내면 하드 리젝트:

```ts
const slTpRequiredValidator: RiskValidator = openIntentRiskValidator((intent) => {
    const sl = intent.metadata?.stopLoss as number | undefined
    const tp = intent.metadata?.takeProfit as number | undefined
    if (sl === undefined || sl === null) {
        return rejectRisk("MT5 orders require a stopLoss. Provide stopLoss with your order.")
    }
    if (tp === undefined || tp === null) {
        return rejectRisk("MT5 orders require a takeProfit. Provide takeProfit (or riskRewardRatio) with your order.")
    }
    return ALLOWED_VALIDATION_RESULT
```

README에 언급된 `maxLossPerPlay`, `shortStrikeDeltaCeiling`, `maxAggregateRiskPercent` 같은 게이트는 정책 검증 단계에서 "engine 파라미터가 게이트보다 느슨할 수 없다"는 불변식으로 강제된다(`README.md:214`: *"policy validation requires the engine value to be no looser than the gate"*). Kill switch는 `packages/convex/convex/lib/killSwitchState.ts`에 전용 모듈로 존재하며 스케줄러 실행 경로 곳곳(`scheduler-runner.ts`, `scheduler-turn-runner.ts`, `state.ts`)에서 참조된다.

**결론: 실제 코드에 하드 리밋 존재.** LLM에게 프롬프트로만 부탁하는 방식이 아니다.

### A.4 브로커/거래소 연동 — 실제 API 엔드포인트

Mock이 아니다. 실제 엔드포인트 상수 확인:

- Alpaca: `packages/alpaca-options/src/runtime-config.ts:34` → `tradingBaseUrl: "https://api.alpaca.markets"`
- MT5: FiveSocket 게이트웨이 경유, `packages/mt5/src/runtime-config.ts:61` → `"https://api.fivesocket.com"` (README에 OpenAPI 계약서 `docs/fivesocket-openapi.json`까지 명시)
- OKX, Polymarket(CLOB/Gamma)도 계정 스코프 자격증명 방식(`credentialEnvPrefix`)으로 실거래 연동 설계(`README.md:96-149`)

다만 저장소 자체에는 라이브 자금이 실제로 투입됐다는 증거는 없다(자격증명은 `private/` 오버레이에 있고 git-ignore됨). Polymarket의 `pm-maker` 엔진은 "dry-run only, policy validation rejects it unless dryRun is exactly true"로 명시적으로 페이퍼 전용이다(`README.md:220`). Codex(LLM) 기반 전략은 "Live Codex execution remains blocked until replay, export audit, and provider-sync evidence has been produced"(`README.md:345`) — 즉 **LLM 주도 전략의 실거래는 저장소 스스로 아직 막아놓은 상태**다. 규칙 기반 엔진(`mechanical-condor`)만 라이브를 상정한 것으로 보인다.

### A.5 MCP / 멀티 venue 어댑터 / reconciliation — 등급(S+) 근거 재검증

**MCP:** `packages/agent/src/mcp/`에 `http-client.ts`, `http-tool-discovery.ts`, `provider-config.ts`, `run-tool-server.ts` 등 17개 파일. 단순 흉내가 아니라 HTTP 기반 MCP 도구 디스커버리, 프로바이더별 allowlist, "destructive/open-world로 태그된 도구는 allowlist에 있어도 차단"(`README.md:290`)까지 구현되어 있다. Fail-closed 설계가 README 문구가 아니라 별도 파일(`mcp-error-sanitizer.ts`, `http-tool-duplicates.ts`)로 뒷받침된다.

**멀티 venue 어댑터:** 4개 venue 각각 독립된 `venue-adapter.ts` 확인 —
`packages/alpaca-options/src/venue-adapter.ts`, `packages/mt5/src/venue-adapter.ts`, `packages/okx/src/venue-adapter.ts`, `packages/polymarket/src/venue-adapter.ts`. 각 패키지가 독립적인 risk-rules, order-helpers, 테스트를 갖춘 진짜 어댑터 계층이다 (예: `packages/agent/src/tools/okx-order-helpers.ts` 829줄 + 테스트 656줄).

**Reconciliation:** `apps/backend/src/provider-account-reconciliation.ts` (37줄, 짧지만 핵심적) — 실행 전 반드시 통과해야 하는 fail-closed 게이트:

```ts
export function assertReconciledProviderAccountState(
    rows: PortfolioFreshnessRow[],
    accountId: string
): void {
    const matching = rows.filter((row) => row.accountId === accountId)
    if (matching.length !== 1) {
        throw new Error(`Execution requires exactly one reconciled provider state for account ${accountId}`)
    }
    const state = matching[0]
    if (!state || state.lastVerifiedAt === undefined || state.stale ||
        state.driftDetected || state.providerStatus !== "healthy" || state.lastError !== undefined) {
        const reason = state?.lastError ?? state?.lastDriftSummary ?? state?.providerStatus ?? "missing provider state"
        throw new Error(`Execution is blocked because account ${accountId} is not cleanly reconciled: ${reason}`)
    }
}
```

`stale`, `driftDetected`, `providerStatus !== "healthy"` 중 하나라도 걸리면 **주문 실행 자체가 예외를 던져 중단**된다 — 브로커 상태(provider truth)가 로컬 장부와 어긋나면 무조건 실행을 막는 fail-closed 설계이며, 이는 프롬프트가 아니라 타입 시스템과 예외로 강제된다.

**검증 결론:** MCP·멀티 venue·reconciliation 3개 주장 모두 실제 코드로 뒷받침된다. 이전 와이드스캔의 S+ 등급은 최소한 "존재 여부" 측면에서는 근거가 있다. 다만 실거래(live) 검증 자체는 저장소가 스스로 아직 완료를 주장하지 않는다(A.4 참조) — 즉 "설계는 S+급이나 실전 검증은 진행 중"이 정확한 요약이다.

### A.6 활동성 / 유지보수 / 실전성 (건강도 신호)

| 신호 | 값 |
|---|---|
| 커밋 수 (전체 히스토리) | 369 |
| 커밋 작성자 | `parlali`(사람, 364커밋) + `noreply@openai.com`(Codex 자동 커밋, 5커밋) — **사실상 1인 프로젝트** |
| 최초/최근 커밋 | 2026-03-23 ~ 2026-09-02 (거의 매일, 매우 활발) |
| GitHub 생성일 / 최근 push | 2026-05-05 생성, 2026-09-02 push |
| 스타 / 포크 / 열린 이슈 | 1 / 0 / 0 |
| CI 워크플로우 | `.github/` 디렉터리 없음 — **CI 파이프라인 부재** |
| 테스트 | 170개 파일 존재, `bun run test`로 로컬 실행 가능하지만 자동화된 CI 게이트는 없음 |
| README 완성도 | 매우 높음 (401줄, 운영 절차·환경변수·안전모델까지 상세) |
| 라이선스 | MIT |

`AGENTS.md`(루트)는 사람이 아니라 AI 코딩 에이전트에게 주는 지침서다 — *"Never duplicate logic... Do not chain silent fallbacks. Fallbacks must be explicit, logged, bounded, and fail closed for execution, ownership, accounting, credentials, and provider identity."* 이는 이 저장소가 **AI 에이전트(Codex/Claude Code류)로 상당 부분 작성된 1인 프로젝트**임을 강하게 시사한다. 즉 "엔터프라이즈 팀이 만든 프로덕션 인프라"가 아니라 "한 명의 운영자가 AI 코딩 에이전트를 활용해 자신의 실거래 인프라를 매우 엄격한 엔지니어링 규율로 구축한 결과물"이다.

**성숙도 판정 (Execution Plane 대상 원 슬롯 기준):**
- 활동성: 높음 (거의 매일 커밋, 살아있는 프로젝트)
- 유지보수: 단일 유지보수자 리스크(bus factor=1), CI 부재는 명백한 결함
- 실전성: 코드 수준 설계는 프로덕션급(fail-closed, reconciliation, kill switch, 정책 검증)이지만, 커뮤니티 검증(1 star/0 fork)과 라이브 자금 운용 증거는 없음. 저장소 스스로 "Live Codex execution remains blocked"라고 명시 — LLM 주도 실거래는 아직 저장소 저자 본인 기준으로도 미검증 단계.

등급: **SCREENED → 조건부 채택 후보 (Execution Plane).** "S+"는 아키텍처 패턴의 우수성에 대한 등급으로는 정당하지만, 커뮤니티/조직적 성숙도까지 포함한 등급이라면 과대평가다.

### A.7 AIOS 시사점

채택할 패턴:
1. **`propose_*` 네이밍 컨벤션 + `executeToolIntent` 단일 관문**: LLM 도구와 실행 파이프라인 사이에 이름부터 명시적 경계를 두는 방식은 AIOS Execution Plane 설계에 그대로 가져갈 만하다.
2. **Reconciliation as a blocking gate, not a dashboard widget**: `assertReconciledProviderAccountState`처럼 "제공자 상태가 깨끗하지 않으면 예외를 던져 실행을 원천 차단"하는 패턴은 AIOS의 브로커 정합성 계층에 직접 이식 가능한 설계.
3. **정책 검증 시점의 불변식 강제** ("엔진 파라미터는 게이트보다 느슨할 수 없다")는 설정 실수로 인한 리스크 게이트 무력화를 원천 차단하는 좋은 아이디어.
4. **MCP fail-closed allowlist + destructive-tool 차단**은 AIOS의 MCP 통합 정책에 참고할 만한 구체적 구현 사례.

주의할 점: 1인 개발 + CI 부재 + 라이브 검증 미완료 상태이므로, AIOS가 이 저장소의 "설계"는 참고하되 "코드"를 그대로 이식하는 것은 별도의 독립적 검증(테스트 재실행, 보안 감사)을 거쳐야 한다.

---

## Part B — Erfaniaa/LLM-Auto-Trader

### B.1 실체 (Marketing vs Reality)

GitHub 설명: *"LLM-driven crypto trading bot built on CCXT plus backtesting tools. Multi-exchange, multi-provider, paper-trade first."* README(`README.md:1-20`)도 스스로 매우 정직하다 — 심지어 "LLM 호출 비용이 알파를 초과하면 무조건 손실"이라는 경제성 경고까지 상세히 서술한다(`README.md:208-253`, "Token cost — read this carefully").

구조: Python 프로젝트(`src/`), CCXT 기반 임의 거래소 지원, Anthropic/OpenAI/Google/OpenAI-compatible 다중 LLM 프로바이더. `src/`만 16,659줄, `tests/` 59개 파일. 다만 저장소 실체의 절반 이상은 **백테스트/리서치 아티팩트**다 — `runs/` 디렉터리 아래 100개 이상의 파라미터 스윕 결과(`summary.json`, `sweep_summary.csv` 등)가 커밋되어 있고, `FINDINGS.md`는 1,700줄 넘는 매우 상세한 CPCV(Combinatorial Purged Cross-Validation)/PBO(Probability of Backtest Overfitting) 기반 리서치 로그다.

**중요한 재평가:** 이 저장소는 "라이브 LLM 자동매매 봇"이라기보다 **"결정론적 규칙 기반 전략을 CPCV/PBO 방법론으로 엄밀하게 검증하는 백테스트 연구 하네스이며, 그 위에 LLM 의사결정 계층을 (아직 완전히 라이브 검증되지 않은 상태로) 얹은 것"**에 가깝다. `FINDINGS.md`는 스스로 이를 인정한다: *"Backtest infrastructure: dispatch script + cached-vote replay are written but require live `claude -p` to populate the VoteCache. A smoke test of one (snapshot, voter) pair against `claude -p` is the next sensible step before any large run."* (`FINDINGS.md:519-522`) — 즉 앙상블 LLM 페르소나 투표 시스템은 설계·테스트는 완료됐지만 실제 대규모 라이브 실행은 스모크 테스트 1건 수준이다.

### B.2 LLM ↔ 결정론적 실행 분리 — 검증됨, 코드 레벨

`docs/design/architecture.md:6-21`이 설계 의도를 명확히 서술:

```
1. LLM decision layer — picks direction (open_long / open_short / hold / ...),
   proposes size, sets stop-loss/take-profit, and tells the scheduler when to
   check next.
2. Deterministic risk layer — accepts or rejects the LLM's proposal based on
   hard caps: leverage ≤ 5×, position size ≤ 25% equity, total exposure ≤ 60%,
   drawdown ≤ 5%, regime-adjusted size multipliers, correlation caps, ...
Why not let the LLM execute directly? Because LLMs hallucinate, and trading
bots that send malformed orders lose money. The deterministic risk layer is
non-negotiable.
```

코드에서 실제 확인: `src/bot.py:123`에서 `RiskManager` 인스턴스화, `src/bot.py:696`에서 `self.risk_manager.validate_response(...)` 호출 — LLM 응답이 파싱된 직후 반드시 이 게이트를 통과해야 한다. `src/risk/circuit_breakers.py:1` 파일 최상단 docstring 자체가 *"Circuit breakers: emergency stops independent of LLM."* — LLM과 물리적으로 분리된 클래스임을 명시. `src/execution/executor.py`는 `ccxt.async_support`를 임포트해 실제 `create_order` 호출(라인 179, 235, 282, 345)을 수행하는 별도 실행 계층이다.

**결론: 실제 코드 경계 있음.** `risk_manager.validate_response()` → `executor.execute()` 순서가 LLM 프로세스와 분리되어 있고, `circuit_breakers.py`는 LLM 상태와 무관하게 독립 작동하도록 설계됨.

### B.3 리스크 컨트롤 — 하드코딩된 숫자 리밋

`src/risk/risk_manager.py:37-58`:

```python
class RiskManager:
    """Master risk gate — ALL LLM actions pass through here before execution.
    The risk manager can:
    - Approve actions as-is
    - Reduce position sizes
    - Reject actions entirely
    - Never INCREASE position sizes or leverage
    """
    def __init__(
        self,
        max_leverage: int = 5,
        max_position_pct: float = 0.25,
        max_total_exposure_pct: float = 0.60,
        max_risk_per_trade_pct: float = 0.02,
        min_reward_risk_ratio: float = 1.5,
        max_concurrent_positions: int = 3,
        ...
```

`config/settings.py:241-254`의 `RiskSettings` 데이터클래스에도 동일한 값이 기본값으로 박혀 있다(`max_leverage: int = 5`, `max_position_pct: float = 0.25`, `max_drawdown_pct: float = 0.05`). `src/risk/circuit_breakers.py:46-92`의 `CircuitBreakers` 클래스는 drawdown, daily loss, rapid loss, volatility spike, spread anomaly, funding rate, margin warning 등 9종의 독립 브레이커 타입을 열거형으로 정의하고 `check_all()`에서 전부 평가한다.

**결론: 프롬프트가 아니라 실제 코드에 숫자 리밋이 존재하며, "risk manager can never increase size/leverage, only reduce or reject"라는 단방향 불변식이 docstring과 구조 양쪽에서 확인된다.** 이는 Part A(trading-agent)보다 오히려 더 단순하고 읽기 쉬운 형태로 같은 패턴을 구현한 예시다.

### B.4 브로커/거래소 연동 — 실코드지만 기본은 페이퍼

`src/execution/executor.py`는 실제 `ccxt.Exchange.create_order()`를 호출하는 실코드이며 Mock이 아니다. 그러나 `BOT_MODE` 기본값이 `paper`이고(`README.md:274`), 라이브 전환에는 `EXCHANGE_API_KEY`/`SECRET`이 명시적으로 필요하다. 즉 **엔진 자체는 실거래 가능하지만, 저장소가 실제 라이브 자금으로 검증됐다는 증거(실거래 로그, 실계좌 스크린샷 등)는 전혀 없다.** README 스스로 "Paper-mode P&L looks too good — it probably is"(`README.md:545-549`)라고 경고하며, `FINDINGS.md` 결론부도 "8-week forward paper trade before live capital"을 계속 다음 단계로 미룬다 — 즉 **저자 본인도 아직 라이브 배포 전 단계**임을 반복적으로 인정한다.

### B.5 MCP / 멀티 venue / reconciliation — 해당 사항 없음 (원래 A등급 근거와 무관)

이 저장소는 MCP를 전혀 사용하지 않는다(코드베이스 전체에 MCP 관련 파일 없음). "멀티 venue"는 MCP형 어댑터가 아니라 **CCXT 라이브러리 하나로 여러 암호화폐 거래소(Binance/Bybit/OKX/MEXC/Gate/KuCoin/Bitget)를 추상화**하는 방식이며, `src/exchange/profile.py`의 `ExchangeProfile`(수수료·마진율 등 거래소별 튜닝값)이 그 실체다 — trading-agent처럼 거래소별 독립 어댑터 패키지를 만드는 방식이 아니라 CCXT의 통일 인터페이스에 얹은 얇은 설정 레이어다. Broker reconciliation(주문/포지션 정합성 확인) 로직도 별도로 존재하지 않는다 — `src/execution/position_manager.py`가 포지션을 추적하지만 trading-agent의 `assertReconciledProviderAccountState` 같은 fail-closed 게이트는 없다.

이 저장소는 애초에 "MCP/멀티 venue adapter/reconciliation"으로 A등급을 받은 것이 아니라 **"LLM 제안 vs 결정론적 리스크 경계 분리 패턴"**으로 A등급을 받았으므로, 이 범위에서는 등급이 정당하다. 다만 등급 근거를 확장 해석해 "멀티 거래소 지원"을 "멀티 venue 아키텍처"로 읽으면 과대평가가 된다 — 실제로는 CCXT가 다 해주는 부분이 크다.

### B.6 활동성 / 유지보수 / 실전성

| 신호 | 값 |
|---|---|
| 커밋 수 (전체 히스토리) | 35 |
| 커밋 작성자 | 1명 (Erfaniaa 단독) |
| 최초/최근 커밋 | 2026-03-17 ~ 2026-07-21 |
| 오늘(2026-09-03) 기준 최근 커밋 경과 | 약 6주 정지 상태 |
| GitHub 생성일 / 최근 push | 2026-03-17 생성, 2026-07-21 push |
| 스타 / 포크 / 열린 이슈 | 36 / 6 / 1 |
| CI | `.github/workflows/ci.yml` 존재 (trading-agent보다 오히려 CI는 갖춰짐) |
| 테스트 | 59개 테스트 파일, README가 "테스트 스위트는 결정론적 — 실 네트워크 호출 없음, CCXT는 경계에서 mock, LLM 응답은 사전 녹화됨"이라고 명시(`README.md:396-397`) |
| README/문서 | 매우 높은 완성도. 토큰 비용 경고, 트레이드오프 섹션 등 실무적으로 정직 |
| 라이선스 | Other (LICENSE 파일 확인 필요, MIT 아님) |

이 저장소의 진짜 가치는 "작동하는 라이브 봇"이 아니라 **"LLM 전략을 어떻게 정직하게 검증할 것인가"에 대한 방법론적 엄밀함**이다 — CPCV, PBO, DSR(Deflated Sharpe Ratio), lookahead 방지(`SnapshotBuilder`가 프롬프트에서 절대 날짜를 제거) 등은 대부분의 "LLM 트레이딩 봇" 저장소에 없는 수준의 통계적 정직성이다. 동시에 `FINDINGS.md`는 스스로 "현재 결정론적 baseline은 롱온리·단일심볼·모멘텀 단일신호로 구조적 한계가 있다"고 인정하고(`FINDINGS.md:308-321`), LLM 전략으로의 전환은 아직 실행되지 않은 다음 단계로 남아 있다.

**성숙도 판정 (Policy Plane 대상 원 슬롯 기준):**
- 활동성: 낮음~중간 (약 6주간 커밋 없음, 프로젝트가 진행형인지 완료 후 방치인지 불명확)
- 유지보수: 단일 유지보수자(bus factor=1), CI는 있으나 이슈 대응 여부 불명(open issue 1건)
- 실전성: 백테스트/리서치 방법론은 매우 견고하나, 라이브 자금 검증은 저자 스스로 미완료라고 밝힘. "LLM 결정 vs 결정론적 리스크"라는 아키텍처 패턴 자체는 코드로 확실히 증명됨.

등급: **SCREENED → 패턴 채택 후보 (Policy Plane), 단 "실전 배포된 시스템"으로 인용하면 부정확.** A등급은 "분리 패턴의 존재와 코드 품질"에 대해서는 정당하나, 저장소 이름이 암시하는 "Auto-Trader"라는 상용성은 과장이다 — 실질은 "LLM-assisted backtest research harness with a paper-trading demo".

### B.7 AIOS 시사점

채택할 패턴:
1. **`RiskManager.validate_response()`를 LLM 파싱 직후 강제 관문으로 두는 최소 구현**은 trading-agent보다 훨씬 단순해서, AIOS Policy Plane의 "가장 작은 참조 구현(reference minimal implementation)"으로 쓰기 좋다.
2. **"risk manager는 사이즈/레버리지를 절대 늘릴 수 없고, 줄이거나 거부만 할 수 있다"는 단방향 불변식**은 문서화하기 쉽고 감사하기 쉬운 안전 속성이라 AIOS 설계 원칙 문서에 그대로 인용할 만하다.
3. **CPCV + PBO + DSR + lookahead 방지(SnapshotBuilder)** 방법론은 AIOS가 자체 전략을 라이브 배포하기 전 검증 파이프라인으로 통째로 가져올 가치가 있다 — 이 저장소에서 가장 성숙하고 독창적인 부분은 사실 "리스크 분리"가 아니라 "백테스트 과최적화 방지 방법론"이다.
4. **비용 정직성**: LLM 호출 비용이 알파를 초과할 수 있다는 경고를 README 최상단 근처에 명시한 태도는 AIOS 문서화 관행으로 참고할 만하다.

주의할 점: "Auto-Trader"라는 이름과 달리 실질적으로 라이브 검증된 자동매매 시스템이 아니라 리서치 하네스에 가깝다는 점을 AIOS 설계 문서에 인용할 때 명확히 구분해야 한다. 앙상블 LLM 투표(`prompts/voters/`, `ensemble_decision.py`)는 스텁 실험에서 오히려 성능이 하락한 사례(`FINDINGS.md:462-495`, 상관된 voter는 다양성 이득이 없다)가 보고되어 있어, AIOS가 유사한 멀티 페르소나 투표 설계를 도입할 때 "voter 다양성 확보"가 선행 조건임을 시사한다.

---

## 종합 비교

| 항목 | A. trading-agent | B. LLM-Auto-Trader |
|---|---|---|
| 언어/스택 | TypeScript, Turborepo, Convex, Next.js | Python, CCXT |
| 규모 | 586 ts 파일, ~20.7k줄(backend만) | ~16.7k줄(src) |
| 커밋/기여자 | 369 / 사실상 1인(+ Codex 자동커밋) | 35 / 1인 |
| 활동성 | 매우 높음 (거의 매일, 오늘까지) | 정지 (6주 무커밋) |
| 스타/포크 | 1 / 0 | 36 / 6 |
| CI | 없음 | 있음 |
| LLM-결정론 분리 | 코드로 확인됨 (`propose_*` → `executeToolIntent` → risk gate → venue adapter) | 코드로 확인됨 (`risk_manager.validate_response()` → `executor.execute()`) |
| 리스크 하드리밋 | 있음, venue별 세분화 | 있음, 단순하고 명확 |
| 브로커/거래소 | 실 API 엔드포인트, 다만 LLM 실거래는 저장소 스스로 미허용 상태 | 실 CCXT 실행 가능하나 기본/사실상 paper 전용 |
| MCP | 있음 (HTTP 기반, allowlist, fail-closed) | 없음 |
| 멀티 venue 어댑터 | 있음 (4개 독립 패키지) | 없음 (CCXT 단일 추상화로 대체) |
| Reconciliation | 있음 (fail-closed 게이트, 코드 확인) | 없음 |
| 가장 강한 부분 | 실행/리스크/정합성의 엔지니어링 규율 | 백테스트 과최적화 방지 방법론(CPCV/PBO/DSR) |
| AIOS 슬롯 | Execution Plane | Policy Plane |

두 저장소 모두 "1인 프로젝트, 낮은 커뮤니티 트랙션"이라는 공통된 성숙도 한계를 가지지만, **"LLM 제안 vs 결정론적 실행/리스크 분리"라는 핵심 아키텍처 주장은 둘 다 코드 레벨에서 실제로 검증된다** — 이는 프롬프트 엔지니어링 수준의 눈속임이 아니라 진짜 모듈 경계다. 반면 trading-agent의 "MCP/멀티venue/reconciliation" 확장 주장과 LLM-Auto-Trader의 "Auto-Trader" 상용성 주장은 각각 정도 차이는 있지만 실전 검증 이전 단계라는 공통된 유보 조건이 붙는다.
