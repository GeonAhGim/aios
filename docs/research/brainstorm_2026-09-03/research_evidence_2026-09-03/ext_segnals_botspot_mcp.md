# Segnals-MCP / BotSpot-MCP 코드 레벨 분석 — AIOS Agent Gateway Plane 4·5번째 데이터 포인트

조사일: 2026-09-03
방법: `git clone --depth 1`로 shallow clone 후 소스 전체 read, GitHub REST API(`gh api`)로 repo 메타데이터(커밋 수, contributor, 생성/push 일자, star/fork) 확인, npm registry / Docker Hub / 제품 도메인 HTTP 상태 확인.

대상:
- A) `eidostein/segnals-mcp` — S+ 등급, "MCP를 통해 account/bot/strategy/portfolio/marketplace capability 노출"
- B) `Lumiwealth/botspot-mcp` — A+ 등급, "자연어/agent에서 전략·backtest·deployment·broker capability 연결"

선행 3개 데이터 포인트 요약(비교 기준):
- **QuantDinger** — MCP는 순수 thin REST proxy, business logic 전무. AIOS Invariant **I-08**로 채택.
- **OBaI** — MCP 서버 자체가 서비스(business logic이 MCP 프로세스 안에 존재), 인증은 admin token 하나뿐. **반면교사(cautionary counter-example)**로 플래그.
- **AgenticTrading** — 브로커가 직접 운영하는 MCP 서버를 OAuth로 사용해 실거래.

---

## 저장소 상태(Health Signal) — 결론부터

두 저장소 모두 **커밋 1개**(`git log --oneline` / GitHub Commits API 모두 1건), **star/fork/watcher 0**이라는 점에서 "충분히 검증된(vetted) 오픈소스 프로젝트"로 보기 어렵다. 세부 내용은 아래 각 Part 서두에서 다룬다.

| 항목 | segnals-mcp | botspot-mcp |
|---|---|---|
| 생성일 → 마지막 push | 2026-06-07 → 2026-06-07 (같은 날) | 2026-04-13 → 2026-04-14 |
| 총 커밋 수 (GitHub API) | 1 | 1 |
| Contributor | 1명, author명 `AI Bot` | 1명 (Robert Grzesik) |
| Star / Fork / Watcher | 0 / 0 / 0 | 0 / 0 / 0 |
| Repo 크기 | 108 KB, TypeScript 소스 포함 | 4 KB, **소스 코드 없음** |
| 라이선스 | MIT | 없음(license: null) |
| npm 게시 여부 | `@segnals/mcp` → `registry.npmjs.org` 조회 결과 `{"error":"Not found"}` (README는 npm 배지·Docker Hub 배지 게시) | 해당 없음(패키지 아님) |
| Docker 이미지 | `hub.docker.com/v2/repositories/segnals/mcp/` → HTTP 404 (README는 GHCR/Docker Hub 이미지 존재 주장) | 해당 없음 |
| 실제 제품 도메인 | `segnals.com` → HTTP 200 (도메인 자체는 실존) | `botspot.trade` → HTTP 200 (Lumiwealth는 lumibot으로 1,300+ star 보유한 실존 조직) |

**결론**: segnals-mcp는 소스 코드·테스트·CI(`​.github/workflows/ci.yml`, lint→typecheck→test 파이프라인)까지 잘 짜여진 "완성형 코드"이지만, 저장소 메타데이터(단일 커밋, author `AI Bot`, 0 star, 생성 당일 push 종료, npm/Docker 배지가 가리키는 아티팩트가 실제로는 존재하지 않음)로 볼 때 **AI로 스캐폴딩된 데모/포트폴리오 성격의 repo**일 가능성이 높다. botspot-mcp는 소스가 전혀 없는 **connector 등록용 stub repo**로, 실제 MCP 서버(`mcp.botspot.trade`)는 폐쇄형 SaaS다. 두 경우 모두 "코드가 곧 진실"이라는 전제가 절반만 성립하므로, 아래 분석은 이 한계를 감안해서 읽어야 한다.

---

## Part A — segnals-mcp

### A-0. 저장소 개요

`src/` 아래 TypeScript로 작성된 stdio 기반 MCP 서버. `tools/`에 기능별 모듈 11개(`meta`, `account`, `stats`, `bots`, `marketplace`, `news`, `copy-trading`, `notifications`, `write-bots`, `write-strategies`, `write-marketplace`), `tests/`에 대응 vitest 파일이 거의 1:1로 존재(총 1,710줄). `client.ts`가 fetch 기반 HTTP 클라이언트, `config.ts`가 env 기반 설정 로더, `errors.ts`가 HTTP status → 타입드 에러 매핑을 담당한다. 코드 품질 자체는 실제 프로덕션 MCP 서버에 준하는 구조(zod 스키마, retry/backoff, timeout, 타입드 에러)를 갖추고 있다.

### A-1. 노출된 MCP 툴 전체 목록 — scoped 설계 확인

README(`README.md:257-297`)와 `src/tools/meta.ts:14-62`의 `TOOL_CATALOG` 상수가 정확히 일치하는 **36개 tool**을 등록한다. 카테고리:

- **Meta**(3): `segnals_whoami`, `segnals_get_capabilities`, `segnals_get_safety_disclaimer` — 스코프 불필요("any valid key")
- **Account**(4): `get_account`, `get_subscription`, `list_connections`, `get_copy_trading` — `read:account`
- **Stats**(4): `get_dashboard`, `get_pnl_summary`, `get_bot_performance`, `get_trades` — `read:stats`
- **Bots read**(5): `list_bots`, `get_bot`, `get_bot_logs`, `get_strategy_schema`, `explain_config` — `read:bots`
- **Bots write/control**(6): `create_bot`, `update_bot`, `start_bot`, `stop_bot`, `restart_bot`, `delete_bot` — `write:bots`/`control:bots`
- **Strategies**(2): `create_strategy`, `set_indicator_filter` — `write:strategies`/`write:bots`
- **Marketplace read**(3) / **write**(2): `browse_marketplace`, `get_listing`, `my_listings` / `copy_strategy`, `publish_listing`
- **News/Knowledge**(4): `get_news`, `get_sentiment`, `get_market_price`, `search_knowledge`
- **Copy trading control**(1), **Notifications**(2)

`tools/index.ts:29-44`에서 이 11개 모듈이 하나의 `registerAllTools(server, client)`로 조립된다.

```ts
// src/tools/index.ts:29-44
export function registerAllTools(server: McpServer, client: SegnalsClient): void {
  registerMetaTools(server, client);
  registerAccountTools(server, client);
  ...
  registerWriteBotTools(server, client);
  registerWriteStrategyTools(server, client);
  registerWriteMarketplaceTools(server, client);
}
```

**AIOS I-06(capability token / closed scope enum) 관점**: 툴 표면 자체는 **11개 scope enum**(`read:account`, `read:stats`, `read:bots`, `write:bots`, `control:bots`, `write:strategies`, `read:marketplace`, `write:marketplace`, `read:news`, `read:knowledge`, `manage:notifications`)으로 명확히 분절되어 있고, 각 tool의 docstring에 `Requires scope: X`가 명시된다. 이는 "broad/raw access"가 아니라 **닫힌 scope enum + 기능별 1:1 매핑**이라는 점에서 I-06 취지에 부합하는 설계다. 다만 이 scope 검증이 **MCP 서버 프로세스 내부에서 이뤄지지 않는다**는 점이 A-2에서 드러나는 핵심 한계다.

### A-2. 인증/인가 — 신뢰 경계는 어디인가

`src/config.ts:26-28`에서 API 키 포맷만 정규식으로 검사한다.

```ts
export function isValidKeyFormat(key: string): boolean {
  return /^sk_(live|test)_[A-Za-z0-9_-]{16,}$/.test(key);
}
```

`src/client.ts:83-86`에서 모든 요청에 `Authorization: Bearer <key>`를 그대로 첨부해 원격 REST API(`https://segnals.com/api`)로 전달한다.

```ts
const headers: Record<string, string> = {
  Authorization: `Bearer ${this.apiKey}`,
  Accept: "application/json",
};
```

`grep -rn "scope" src/`로 전수 검색한 결과, MCP 서버 코드 안에는 **scope를 실제로 검증하는 로직이 단 한 줄도 없다.** 존재하는 건 (1) tool docstring에 적힌 "Requires scope: X"라는 사람이 읽는 설명, (2) `errors.ts:39-48`의 `ScopeError` 클래스뿐이며, 이 `ScopeError`는 원격 API가 403을 반환할 때 body의 `required` 필드를 그대로 되던지는 용도다.

```ts
// src/client.ts:219-223
case 403: {
  const requiredScope = (body.required as string) ?? "unknown";
  throw new ScopeError(requiredScope);
}
```

즉 **scope enforcement의 trust boundary는 100% 원격 Segnals REST API 쪽**에 있고, MCP 서버는 "이 tool은 이 scope가 필요하다"는 메타데이터만 LLM에게 안내하는 passthrough다. 이는 정확히 **QuantDinger의 thin-proxy 패턴**과 일치한다 — MCP 프로세스는 (a) 요청 형태를 REST endpoint로 번역하고 (b) 응답 status code를 사람이 읽을 수 있는 에러 메시지로 재포장할 뿐, 인가 판단(authorization decision)은 갖고 있지 않다. README의 아키텍처 다이어그램(`README.md:344-363`)도 이를 명시적으로 그린다.

```
AI Agent / IDE → stdio → Segnals MCP Server (Local) → HTTPS+Bearer → Segnals REST API (Remote)
                                                         (Enforces Rate-Limits, Scopes, and Auditing)
```

**OBaI형 "MCP가 곧 서비스"** 패턴과는 명확히 다르다. OBaI는 business logic이 MCP 프로세스 안에 있었고 인증도 admin token 하나로 뭉뚱그려져 있었던 반면, segnals-mcp는 인가/rate-limit/audit을 모두 원격 API에 위임하고 로컬 프로세스는 무상태(stateless) 번역기로 남는다. AIOS I-08("MCP as pure thin proxy")의 **긍정 사례**로 채택할 수 있는 근거가 된다.

### A-3. Mutating 호출의 deterministic gate 여부 — "two-step confirmation"의 실체

README와 AGENTS.md는 `create_bot`, `delete_bot`, `copy_strategy` 등 위험한 tool에 "Two-Step Confirmation Gate"가 있다고 강조한다. 실제 구현(`src/tools/write-bots.ts:57-106`)을 보면:

```ts
// src/tools/write-bots.ts:66-82
{
  exchange: z.enum(["bybit", "phemex", "mt5"]).describe("Exchange to create the bot for"),
  confirm: z.boolean().optional().default(false).describe("Set to true to execute after previewing"),
},
async ({ exchange, confirm }) => {
  try {
    if (!confirm) {
      return ok({ action: "create_bot", preview: `Will create...`, ... });
    }
    const result = await client.post<{ msg: string; bot_id: number }>(
      "/bots/create", { exchange, client: "mcp" },
    );
```

`delete_bot`(`write-bots.ts:299-346`)도 동일 패턴이다. 핵심 문제는 **"confirm" 파라미터가 LLM의 tool-call argument일 뿐, 서버가 독립적으로 검증할 수 있는 결정론적 게이트가 아니라는 점**이다.

- preview 응답에는 **token/nonce가 전혀 없다.** 같은 대화의 이전 turn에서 실제로 preview를 호출했는지, 사용자가 실제로 "confirm"이라고 답했는지는 **MCP 서버도, 원격 REST API도 검증하지 않는다.**
- LLM은 첫 호출부터 `confirm: true`를 보낼 수 있으며, 코드상 이를 막는 장치는 없다. 즉 "2단계"는 **prompt-level 관례(convention)**이자 UX 장치이지, **서버가 강제하는 gate가 아니다.**
- 유일한 진짜 서버측 검증은 `write-bots.ts:127-138`, `write-strategies.ts:61-72`의 **forbidden-key 필터**(`API_KEY`, `API_SECRET`, `MT5_PASSWORD` 등 자격증명 키워드를 config에서 하드 차단)뿐이며, 이는 confirm 여부와 무관하게 항상 실행된다.

```ts
// src/tools/write-bots.ts:127-138 (update_bot)
const forbiddenKeys = ["API_KEY", "API_SECRET", "api_key", "api_secret", "MT5_PASSWORD"];
const rejected = Object.keys(config || {}).filter(k => forbiddenKeys.includes(k));
if (rejected.length > 0) {
  return ok({ action: "update_bot", preview: "REJECTED: Config contains exchange credentials.", ... });
}
```

즉 **자금 이동/자격증명 노출 같은 "하드 불변식"은 코드로 강제**되어 있으나, **"사용자가 실제로 승인했는가"라는 확인은 순수 소프트 컨벤션**이다. `segnals_stop_bot`처럼 "안전한" 액션은 confirm 자체를 요구하지 않는다(`write-bots.ts:226-252`, 주석: "This is a safe action — no confirmation required"). 결론적으로, LLM tool-call은 (자격증명 필터를 제외하면) **바로 execution으로 연결**되며, 사람이 개입하는 지점은 "LLM이 스스로 confirm:false로 먼저 부르고 사용자 답을 기다리는" 행동을 신뢰하는 것뿐이다 — 이는 **prompt injection이나 LLM의 판단 오류에 취약한 지점**이다.

### A-4. Marketplace 노출 — provenance/신뢰 신호

`src/tools/marketplace.ts:14-44`의 `segnals_browse_marketplace` docstring은 "performance data (clearly labeled as live or backtest)... Results are neutrally sorted"라고 명시하고, `write-marketplace.ts:60-67`의 preview 응답은 리스팅 카드에 `seller_name`, `exchange`, `perf_source`(실거래/백테스트 구분 필드로 추정)를 포함시킨다.

```ts
// src/tools/write-marketplace.ts:60-67
listing: {
  id: listingInfo.id,
  title: listingInfo.title,
  seller_name: listingInfo.seller_name,
  exchange: listingInfo.exchange,
  price_usd: price,
  perf_source: listingInfo.perf_source,
},
```

publish 경로(`write-marketplace.ts:125-183`)는 admin review를 거치는 **명시적 gate**를 갖는다:

```ts
// src/tools/write-marketplace.ts:143-157
preview: `Will publish bot #${source_bot_id} as marketplace listing '${title}'. ` +
  ... +
  " The listing will be submitted for admin review before becoming active." +
  " Credentials are automatically scrubbed from the config snapshot.",
...
// 실행 후
status: "pending_review",
note: "An admin will review the listing. Once approved, it will appear in the marketplace.",
```

즉 marketplace publish는 **사람(admin)이 개입하는 review queue**가 실제 서버측(REST API) 워크플로로 존재한다고 문서화되어 있다 — 이는 A-3의 "confirm" 패턴보다 한 단계 더 견고한 gate(제출 즉시 활성화되지 않고, 사후 검토를 거침)다. 다만 `copy_strategy`(구매 측)의 유료 전략 결제는 crypto 결제(NOWPayments)로 처리되며(`write-marketplace.ts:69-71`, `107-118`), 결제 완료 검증 자체는 MCP 코드 밖(webhook 등 REST API 내부)에서 일어나므로 이 저장소만으로는 검증 불가능하다.

### A-5. AIOS 시사점 (Agent Gateway Plane / I-06, I-08)

1. **패턴 판정**: segnals-mcp는 **QuantDinger형 thin-proxy 패턴**에 해당한다. 인가(scope 검증), rate limiting(120 req/min, README `Security Model` 항목 5), audit이 전부 원격 REST API 쪽에 있고 MCP 프로세스는 무상태 번역기다. OBaI형 "MCP가 곧 서비스" 패턴이 아니다. I-08("MCP as pure thin proxy, zero business logic")과 부합하는 **5번째 실증 사례**로 채택 가능하나, `detectRiskyConfig()`(martingale+leverage 경고, `write-bots.ts:21-55`) 같은 위험도 판정 로직이 MCP 레이어에 일부 존재한다는 점에서 "zero business logic"이 100% 순수하지는 않다 — thin-proxy 원칙에 **safety-annotation 레이어**를 얹은 절충형으로 기록하는 것이 정확하다.
2. **I-06(capability token / closed scope enum) 관점**: tool 표면은 11개 scope enum으로 잘 분절되어 있어 참고할 만한 **긍정 사례**다. 다만 **scope enum이 tool docstring에만 존재하고 MCP 서버가 로컬에서 검증하지 않는다**는 점은 AIOS Gateway Plane 설계에 중요한 반면교사다: Gateway가 REST API를 신뢰해 401/403을 그대로 전달하는 것 자체는 무방하나, Gateway가 자체적으로 scope-tool 매핑을 **사전 검증(local pre-check)**해서 불필요한 원격 호출과 latency, 그리고 "권한 없는데도 tool이 LLM에게 노출되어 시도되는" 노이즈를 줄이는 게 더 안전하다. AIOS는 Gateway Plane에서 **capability token → 로컬 scope 사전 필터 → REST 호출**의 2중 검증을 I-06에 명문화할 근거를 여기서 얻는다.
3. **Mutating 호출 게이팅**: "confirm: true" 패턴은 **결정론적 게이트가 아니라 LLM 자기신고(self-report) 패턴**이며, AIOS가 반면교사로 삼아야 할 지점이다. 진짜 deterministic gate라면 (a) preview 호출이 서버측에 상태(pending_action id/nonce)를 남기고, (b) confirm 호출이 그 nonce를 참조해야 하며, (c) nonce는 TTL을 가져야 한다. segnals-mcp는 이 중 어느 것도 구현하지 않았다 — AIOS의 Agent Gateway Plane에서 "위험 action 2단계 확인"을 설계할 때는 **서버측 pending-action 저장 + nonce 기반 confirm**을 반드시 요구해야 한다는 설계 원칙(가칭 I-09 후보)을 도출할 수 있다. 반면 marketplace publish의 admin-review queue는 훨씬 견고한 human-in-the-loop 모델로, 참고할 가치가 있다.
4. **자격증명 격리**는 코드로 강제된 유일한 하드 불변식이다(forbidden-key 필터 + "exchange secret은 대시보드에서만 입력, MCP는 절대 못 봄"). 이 설계 — **"에이전트가 설정은 바꿀 수 있어도 credential 자체는 절대 볼 수 없다"** — 는 AIOS Gateway Plane의 핵심 불변식으로 그대로 채택할 만하다.
5. **주의**: 위 모든 코드는 실재하지만, 저장소 자체가 단일 커밋·0 star·미게시 npm/Docker 아티팩트라는 점에서 "실전에서 검증된 패턴"이 아니라 "설계 의도가 잘 드러난 참고용 샘플 코드"로 등급을 낮춰 인용해야 한다.

---

## Part B — botspot-mcp

### B-0. 저장소 개요 — 코드가 없다

`git clone --depth 1`로 받은 전체 내용은 `README.md`(122줄), `glama.json`(4줄), `mcp.json`(10줄), `server.json`(17줄), `smithery.yaml`(17줄)뿐이다. `src/`, `tests/`, `package.json` 등 **구현 코드가 전혀 없다.** 이 5개 파일은 모두 MCP 레지스트리(Smithery, Glama, Cursor Directory, 공식 MCP registry)에 서버를 등록하기 위한 **manifest/connector 설정 파일**이며, 실제 서버는 이 저장소 밖의 호스티드 SaaS(`https://mcp.botspot.trade`)에서 구동된다.

```json
// server.json:10-17 — 공식 MCP registry manifest
"repository": { "url": "https://github.com/Lumiwealth/botspot-mcp", "source": "github" },
"version": "1.1.0",
"remotes": [ { "type": "streamable-http", "url": "https://mcp.botspot.trade/mcp" } ]
```

따라서 **A항 같은 코드 레벨 tool 스키마·인가 로직 분석은 이 저장소 자체로는 불가능**하다. 아래 내용은 저장소에 담긴 문서(README)가 자기 서술(self-description)한 내용을 근거로 하며, "코드로 검증된 사실"이 아니라 "벤더가 문서로 주장하는 사양"임을 명확히 구분해서 읽어야 한다.

### B-1. 노출된 MCP 툴(문서 기준) — 32개, 6개 카테고리

README `## 32 MCP Tools` 표(`README.md:70-81`)가 유일한 tool 목록 소스다.

```markdown
| Category | Tools |
|----------|-------|
| Strategy Generation | generate_strategy, refine_strategy, generation_status, generate_other_code |
| Strategy Management | list_strategies, get_strategy, update_strategy, delete_strategy, get_code, list_revisions, set_strategy_revision |
| Backtesting | start_backtest, stop_backtest, backtest_status, list_backtests, get_backtest_artifact, query_csv |
| Deployment | list_deployments, get_deployment_logs, get_bot_performance, get_bot_positions, get_portfolio_series |
| Visuals | get_strategy_visuals, get_backtest_visuals, get_backtest_chart_series |
| Marketplace | list_public_bots, share_strategy, publish_to_marketplace, unpublish_from_marketplace, clone_strategy |
| Account | get_account_status, get_tool_call_metrics |
| Data | set_data_provider |
```

이 표면은 **broker capability(주문 실행·자금 이동)를 직접 노출하는 tool이 하나도 없다** — `list_deployments`, `get_deployment_logs`, `get_bot_performance`, `get_bot_positions`, `get_portfolio_series`는 모두 **read-only 모니터링**이다. "10+ 브로커에 실배포"라는 기능은 배포 파이프라인(코드 생성 → backtest → deploy) 내부에서 이뤄지고, MCP tool 표면에는 배포 상태 조회/제어만 노출된다. 즉 **broker 자체의 raw API가 아니라 BotSpot 플랫폼이 한 겹 감싼 상위 capability**만 노출되는 구조로, scope 관점에서는 QuantDinger·segnals-mcp보다도 더 좁게 닫혀 있는 편이다(단, 이는 코드가 아니라 README 표만으로 확인한 것이라는 한계가 있다).

### B-2. 인증/인가 — 문서상 주장

README `## Technical Details`(`README.md:100-107`):

```markdown
- **Transport:** Streamable HTTP (JSON response mode)
- **Auth:** OAuth 2.1 with Dynamic Client Registration (browser) or API key bearer token (CLI/IDE)
- **Endpoints:**
  - `https://mcp.botspot.trade/mcp` — Full tool set (Claude, CLI, IDE)
  - `https://mcp.botspot.trade/mcp/public` — Commerce-filtered (ChatGPT Apps SDK)
- **OAuth Discovery:** `https://mcp.botspot.trade/.well-known/oauth-protected-resource`
```

`mcp.json`(`mcp.json:5-6`)도 `"transport": "streamable-http", "auth": "oauth2"`로 동일하게 명시한다. 인증 자체는 **OAuth 2.1 + Dynamic Client Registration** — segnals-mcp의 "raw API key만 env로 주입"보다 한 단계 더 표준화된 방식이며, AgenticTrading이 썼던 "브로커의 원격 MCP 서버를 OAuth로 사용"하는 패턴과 동일 계열이다. 다만 **엔드포인트를 두 개로 분리**(`/mcp` 전체 세트 vs `/mcp/public` "commerce-filtered")한 점이 특징적이다 — 이는 클라이언트(신뢰도)에 따라 **노출 tool 집합 자체를 서버에서 다르게 구성**하는 설계로, ChatGPT Apps SDK처럼 상대적으로 덜 신뢰되는 host에는 축소된 tool 표면을 제공한다는 뜻이다. 이는 코드 레벨로 확인할 수 없지만, **엔드포인트 분리를 통한 client-tier별 scope 축소**라는 아이디어 자체는 AIOS Gateway Plane 설계에 참고할 만하다.

인가(authorization)의 trust boundary는 명백히 **원격 SaaS 서버 안**에 있다 — 이 저장소에는 그 로직이 전혀 없으므로 "thin proxy냐 MCP-as-service냐"라는 질문 자체가 이 repo 수준에서는 성립하지 않는다. 실제 서버(`mcp.botspot.trade`)가 REST API 위의 thin gateway인지, 아니면 strategy-generation/backtest 엔진을 직접 구동하는 "MCP가 곧 서비스"형인지는 **폐쇄소스라 검증 불가능**하다. 다만 tool 목록에 `generate_strategy`, `start_backtest` 같은 **장시간 비동기 작업**과 그 상태를 조회하는 `generation_status`, `backtest_status` tool이 별도로 존재하는 것으로 보아, MCP endpoint 자체가 job-queue 오케스트레이션(즉 상당한 business logic)을 갖고 있을 가능성이 높다 — 이 경우 실질적으로는 **OBaI형에 가까운 "MCP 프로세스(정확히는 그 뒤 SaaS)가 서비스 그 자체"** 패턴일 개연성이 있으나, 코드가 없으므로 이는 추정이지 확인된 사실이 아니다.

### B-3. Mutating 호출의 게이트 — 확인 불가

`start_backtest`, `publish_to_marketplace`, `update_strategy`, `delete_strategy` 같은 mutating tool에 confirm 파라미터나 인간 승인 단계가 있는지는 **README에 전혀 언급이 없다.** segnals-mcp처럼 "Two-Step Confirmation"을 마케팅 포인트로 내세우지도 않는다. 따라서 LLM tool-call이 실행으로 직결되는지 여부를 **코드로도 문서로도 확인할 수 없다** — 이는 그 자체로 리스크 신호다(안전장치가 있다면 벤더가 광고했을 가능성이 높은데, 없다).

### B-4. "자연어 → strategy" 파이프라인의 실체

README(`README.md:7-9, 50-51`):

> "Generate trading strategies from plain English, backtest on real historical data, and deploy live to 10+ brokers... The AI generates production-ready Python code using [Lumibot]"

tool 표(`generate_strategy`, `refine_strategy`, `generation_status`, `generate_other_code`)로 미루어, 이는 **파라미터화된 thin wrapper가 아니라 실제 코드 생성 파이프라인**으로 보인다 — `generate_strategy`가 LLM(서버측에 내장된 또 다른 코드-생성 모델일 가능성)을 호출해 Lumibot 기반 Python 전략 코드를 실제로 만들고, 비동기이므로 `generation_status`로 폴링하며, `refine_strategy`로 반복 수정하는 구조다. 단순히 "미리 정의된 전략 템플릿에 파라미터만 꽂아 넣는" thin parameterized tool 세트가 아니라, **code synthesis + backtest + deployment의 실제 lifecycle**을 오케스트레이션하는 것으로 문서상 확인된다. 다만 이 판단 역시 **폐쇄소스 서버의 README 자기 서술에 전적으로 의존**한 것이며, 생성된 코드의 품질·안전성 검증(sandboxing, 코드 실행 전 정적 분석 등)이 실제로 존재하는지는 이 저장소만으로는 전혀 알 수 없다.

Lumiwealth 조직 자체는 `lumibot`(오픈소스, 1,300+ star, MIT)이라는 실적 있는 백테스팅 프레임워크를 보유한 신뢰도 있는 조직이나, botspot-mcp/botspot.trade는 **그 프레임워크 위에 얹은 별도의 상용 SaaS**이며 코드가 폐쇄되어 있어 "실제 파이프라인"과 "마케팅 문구"를 구분할 근거가 부족하다.

### B-5. AIOS 시사점 (Agent Gateway Plane / I-06, I-08)

1. **패턴 판정 자체가 불가능**: thin-proxy(QuantDinger)냐 MCP-as-service(OBaI)냐를 가릴 소스 코드가 없다. 굳이 추정한다면, 비동기 job(`generate_strategy`/`generation_status`, `start_backtest`/`backtest_status`) 패턴과 두 개의 auth-tier별 endpoint 분리(`/mcp` vs `/mcp/public`) 설계는 **OBaI형에 가까운 "MCP 엔드포인트가 상당한 business logic·상태를 보유"하는 3번째 변종**일 가능성이 있다는 정도만 기록해야 한다. AIOS 문서에는 이를 "미확인(unverified) — 폐쇄소스"로 명시하고, 확정적 분류에서 제외하는 것이 정확하다.
2. **엔드포인트 분리를 통한 tier별 scope 축소**(`/mcp` full vs `/mcp/public` commerce-filtered)는 코드로 검증하지 못했음에도 **아이디어 자체는 I-06에 참고할 가치**가 있다: AIOS Gateway Plane도 client 신뢰도(예: 사내 admin 클라이언트 vs 외부 파트너 vs 소비자용 앱)에 따라 서로 다른 tool 카탈로그를 노출하는 다중 진입점을 설계 옵션으로 검토할 수 있다.
3. **Mutating action의 안전장치가 문서에 전혀 없다**는 사실 자체가 반면교사다 — AIOS가 3rd-party MCP를 Gateway Plane에 연동할 때, "벤더가 광고하지 않는 안전장치는 존재하지 않는다고 가정"하는 것을 기본 태도(default-deny assumption)로 삼아야 한다. Segnals가 (약하게나마) confirm 패턴을 광고한 것과 대비된다.
4. **저장소 실사(due diligence) 관점의 시사점**: "실제 서비스가 있고 조직이 신뢰할 만하다"는 사실(Lumiwealth/lumibot)이 "그 조직이 내놓은 특정 MCP connector 저장소의 코드가 검증 가능하다"는 것을 보장하지 않는다. AIOS가 외부 MCP를 카탈로그에 편입할 때는 **(a) 저장소에 실행 가능한 소스가 있는가, (b) 있다면 코드 레벨로 scope·gate를 검증했는가, (c) 없다면 폐쇄형 SaaS로 명시하고 별도의 벤더 신뢰 평가(계약·SOC2·pentest 리포트 등) 트랙으로 넘긴다**는 3단계 판별 절차를 Gateway Plane 온보딩 체크리스트에 넣을 근거가 된다.

---

## 종합 비교표

| 항목 | segnals-mcp | botspot-mcp | QuantDinger(기존) | OBaI(기존) |
|---|---|---|---|---|
| 코드 존재 | O (TS, 108KB, test 1,710줄) | X (config만 4KB) | O | O |
| Tool 수 | 36 | 32 (문서 기준) | — | — |
| Scope 모델 | 11개 scope enum, docstring 명시 | 문서에 scope 개념 없음(OAuth 범위만) | — | 없음(admin token 1개) |
| Scope 검증 위치 | 원격 REST API(서버는 passthrough) | 확인 불가(폐쇄소스) | 원격 REST API | MCP 프로세스 내부 |
| Mutating gate | `confirm:true` 파라미터(비결정론적, nonce 없음) + admin-review(marketplace publish만 견고) | 문서에 언급 없음 | 없음(원 자료 기준) | 없음 |
| 판정 패턴 | **thin-proxy(QuantDinger형) + safety-annotation 절충** | **불명(폐쇄소스); 추정컨대 OBaI형에 가까운 변종** | thin-proxy | MCP-as-service |
| 저장소 신뢰도 | 낮음(단일 커밋, 0 star, 미게시 아티팩트) | 해당 없음(connector stub); 배후 조직은 신뢰도 있음 | — | — |

**AIOS 액션 아이템 제안**:
- I-08(thin-proxy) 원칙에 segnals-mcp를 5번째 실증 사례로 추가하되, "safety-annotation은 thin-proxy 예외로 허용"이라는 단서를 명문화.
- I-06(closed scope enum)에 "scope enum은 tool 카탈로그 문서화뿐 아니라 Gateway 자체의 로컬 사전 검증에도 사용되어야 한다"는 요구사항 추가.
- 신규 후보 불변식(가칭 I-09): "위험 action의 confirm/2-step 패턴은 서버측 nonce/pending-action 저장을 동반해야 하며, LLM이 스스로 신고하는 boolean 플래그만으로는 deterministic gate로 인정하지 않는다."
- 외부 MCP 온보딩 체크리스트에 "소스 존재 여부 확인 → 없으면 폐쇄형 SaaS로 별도 트랙 처리" 절차 추가(botspot-mcp 사례 근거).
