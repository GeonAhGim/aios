# AgenticTrading / OBaI 코드 레벨 분석 — 엔터프라이즈 트레이딩 OS 설계 참고

조사 대상:
- `ext/AgenticTrading` (Open-Finance-Lab, "Agentic Trading Lab")
- `ext/obai` (OBaI, "Open-source multi-agent platform for stock research")

두 저장소 모두 read-only로 조사했으며, 아래 경로는 각 스크래치패드 클론 루트(`.../scratchpad/ext/AgenticTrading`, `.../scratchpad/ext/obai`) 기준 상대경로다.

---

## Part A. AgenticTrading

### 메타데이터

| 항목 | 값 |
|---|---|
| License | **OpenMDW License Agreement v1.0** (SecureFinAI Lab 저작권) — 모델/데이터/코드용으로 쓰이는 비교적 생소한 오픈 라이선스. 표준 OSI 라이선스가 아니므로 상업적 재배포 조건을 계약 검토 필요 |
| 언어 | Python (대시보드 백엔드 + FinAgents 멀티에이전트 오케스트레이션), 프런트엔드(대시보드) 별도 |
| 규모 | Python 파일 877개, 약 278,000 LOC, 저장소 전체 81MB |
| 최근 커밋 | `43ab8e6` — "Merge pull request #438 from .../feature/platform-provider-failover", 2026-09-02 (거의 매일 커밋되는 활성 프로젝트) |

리포는 두 개의 이질적인 서브시스템으로 구성되어 있다: (1) `dashboard/` — 일반 사용자용 웹 대시보드 겸 백테스트/페이퍼·라이브 트레이딩 서비스, (2) `orchestration/FinAgents/` — 별도의 리서치용 멀티에이전트 오케스트레이션 프레임워크(alpha/risk/portfolio/execution agent pool, MCP 기반). 두 서브시스템은 코드 경계가 뚜렷하고 대시보드가 실질적인 "제품"에 가깝다.

### 1. Agent 스키마

에이전트는 두 계층으로 정의된다.

**(a) 마켓플레이스 템플릿** — `dashboard/config/marketplace.json`, config-driven JSON. 필수 필드: `template_id`, `name` (누락 시 스킵). 선택: `description`, `category`(`prompting_llms`|`us_stocks`|`cn_ashares`, 그 외 값은 미분류로 처리), `model_name`(예: `anthropic/claude-haiku-4-5`), `tags`, `author`, `repo_url`, `pipeline`(단계별 `id`/`presetKey`/`prompt`/`outputFormat`) 또는 `runtime_type`+`runtime_config`(호스티드 런타임용).

```json
{
  "template_id": "ai-hedge-fund",
  "name": "AI Hedge Fund",
  "model_name": "nvidia/nemotron-3-nano-30b-a3b",
  "category": "us_stocks",
  "author": "virattt / Agentic Trading Lab",
  "repo_url": "https://github.com/virattt/ai-hedge-fund",
  "runtime_type": "ai_hedge_fund",
  "runtime_config": { "analysts": ["fundamentals_analyst", "technical_analyst", ...] }
}
```
(`dashboard/config/marketplace.json`)

**(b) 영속화된 에이전트(agent record)** — `dashboard/backend/domain/agents/repository.py:113` SQLite 스키마 `external_agents` 테이블. 필드: `agent_id`, `name`, `session_id`, `api_key_hash`/`api_key_prefix`(원문 키는 저장하지 않음), `model_name`, `scopes`(기본값 `agents:register,runs:write,context:read,decisions:write,runs:read`), `owner_user_id`/`owner_browser_session`, `runtime_type`/`runtime_config`, `category`, `agent_type`(`builtin`|`external`), `pipeline_config`, `cash_allocation`/`backtest_allocation`, **`live_trading_enabled`**(BOOL, 기본 0).

버저닝은 별도 `create_version()` (`dashboard/backend/domain/agents/service.py:811`)으로 구현되어 있으며 필드가 상당히 정교하다: `version`, `execution_mode`, `architecture`, `model_backbones`(리스트), `decision_frequency`, `code_commit`, `prompt_hash`, `config_hash`, `verification_level`. 즉 "어떤 코드/프롬프트/설정 조합이 어떤 검증 수준으로 실행되었는가"를 해시로 고정하는 provenance 지향 스키마다(다만 verification_level이 실제로 무엇을 검증하는지는 이번 조사 범위에서 深掘りしていない).

### 2. 마켓플레이스

문서: `docs/source/lab/marketplace.rst`. 구현: `dashboard/backend/domain/agents/marketplace.py`, `dashboard/backend/domain/agents/service.py:583` (`clone_marketplace_template`).

- **저장/카탈로그**: 단일 JSON 파일(`dashboard/config/marketplace.json`)을 `lru_cache`로 프로세스 메모리에 캐시(`_load_catalog`, `marketplace.py:61`). DB 스키마 마이그레이션 없이 PR만으로 템플릿 추가 가능 — 즉 "카탈로그 = git 저장소의 정적 파일", 별도 마켓플레이스 서비스/DB 없음.
- **설치 플로우**: "Add to My Agents" → `clone_marketplace_template()` → 사용자 소유의 `builtin` 에이전트로 파이프라인을 복사(`_create_builtin_copy`). 복제본은 완전히 독립적이며 원본 템플릿 수정이 복제본에 영향을 주지 않음.
- **서명/출처 검증 — 없음.** 코드 주석이 이를 명시적으로 인정한다:
```python
# No whitelist, on purpose: create_agent doesn't validate model_name
# either, so rejecting here would be inconsistent, ...
```
(`service.py:604`) — `model_name`, `category`, `pipeline` 어느 것도 화이트리스트/서명 검증이 없다. `repo_url`은 `https://github.com/`로 시작하는지만 문자열로 확인(`marketplace.py:56`). 즉 마켓플레이스는 "코드 배포"가 아니라 "프롬프트 텍스트 배포"이므로 공급망 공격 표면은 프롬프트 인젝션 수준으로 제한되지만, 그 대신 임의 프롬프트 내용에 대한 검증이 전혀 없다.
- **실행 격리**: 템플릿의 `pipeline`은 순수 텍스트 프롬프트일 뿐 코드가 아니므로 실행 자체는 항상 플랫폼의 LLM 호출 경로를 통과한다. 예외가 `runtime_type: ai_hedge_fund`인데, 이건 실제 서드파티 Python 패키지(`virattt/ai-hedge-fund`)를 구동한다 — 이 경우의 격리는 아래 §4 참조.
- **크리덴셜 경계**: `dashboard/backend/domain/agents/runtime.py:100` 주석: "Interpreter paths and credentials intentionally are not agent config. They are deployment-owned environment settings, preventing a stored agent from selecting arbitrary executables or smuggling secrets into run metadata." 즉 에이전트 설정(JSON)에는 자격증명이나 실행 파일 경로가 절대 들어갈 수 없고, 이는 배포 환경변수로만 존재하는 구조적 분리.

### 3. 백테스트 실행 엔진

핵심: `dashboard/backend/domain/backtesting/engine.py` `class HourlyBacktester`(대시보드 경로), 별도로 `orchestration/FinAgents/agent_pools/backtest_agent/backtest_agent.py`(FinAgents 경로, Qlib 기반).

- **데이터 피드 추상화**: `dashboard/backend/infrastructure/market_data/profiles.py`의 `MarketProfile`(예: `ALPACA`) — 거래소별 규칙(중국 A주 T+1 등)까지 프로파일화. 지표는 `domain/backtesting/features.py::TechnicalIndicators`.
- **결과/지표**: `domain/backtesting/metrics.py`는 Sharpe·MaxDrawdown 두 개뿐(경량). 반면 FinAgents 쪽 `backtest_agent.py`는 VaR/CVaR/Sortino 등 더 풍부한 리스크 지표 계산 함수를 갖고 있음(`calculate_advanced_risk_metrics`).
- **Look-ahead/leakage guard**: 엔진은 매 스텝마다 "결정 시점 이전의 가장 최근 거래일"을 명시적으로 계산해 컨텍스트에 주입한다.
```python
def _prior_market_date_by_decision_date(timestamps) -> Dict[date, Optional[date]]:
    """Map each ATL trading date to its latest strictly earlier trading date."""
```
(`engine.py:133`) 이 값(`latest_market_date_before_decision`)이 `AgentRuntimeContext`에 실려 LLM/런타임에 전달되므로, 당일 마감 이후 데이터가 결정에 유입되는 경로를 구조적으로 차단한다(`engine.py:1161`). 다만 이는 "당일 데이터 차단"이지 파라미터 최적화 단계의 데이터 스누핑까지 막는 장치는 아니다.
- **OOS/walk-forward**: 대시보드의 메인 백테스트 루프에는 walk-forward가 없다. 반면 FinAgents `backtest_agent.py:2142`의 `run_walk_forward_analysis(strategy_id, window_size=252, step_size=21)`는 Qlib이 설치된 경우에만 동작하며, 각 윈도우를 독립적으로 재실행할 뿐 — 사실상 "구간별 성과 분해(segmented performance)"에 가깝고, 훈련/검증 분리·재적합(refit)을 수반하는 엄밀한 walk-forward는 아니다.

### 4. 외부 Agent API / LLM 연동

- **LLM 게이트웨이**: `dashboard/backend/infrastructure/llm/providers/anthropic_native.py`(네이티브 Anthropic), `providers/openrouter.py`(OpenRouter 경유 멀티 프로바이더), `execution/adapters/openai.py` 등 다중 프로바이더 어댑터 레지스트리.
```python
def make_client(anthropic_cls: Any) -> Optional[Any]:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    ...
```
(`anthropic_native.py:16`) — 키는 서버 프로세스의 환경변수에만 존재, 에이전트 레코드에는 저장되지 않음.
- **외부 에이전트 API**: `dashboard/backend/domain/agents/repository.py`의 `external_agents` 스키마가 곧 API 계약. `scopes` 컬럼으로 세분화된 권한(`agents:register`, `runs:write`, `context:read`, `decisions:write`, `runs:read`)을 부여하는 capability 모델.
- **Paper vs Live 게이팅**: 에이전트별 `live_trading_enabled` BOOL 플래그가 실제 라이브 주문 경로의 유일한 게이트다.
```python
if not agent.get("live_trading_enabled"):
```
(`dashboard/backend/execution/robinhood_live_service.py:675`) 라이브 실행은 Robinhood의 자체 MCP 서버(`agent.robinhood.com/mcp/trading`)를 OAuth(PKCE)로 호출하는 구조(`infrastructure/brokers/robinhood_mcp.py`, `robinhood_oauth.py`) — 브로커 자체가 MCP 표준으로 에이전트에 주문 도구를 노출하는, 매우 최신 형태의 브로커 통합이다.
- **리스크 게이트(라이브 경로)**: `robinhood_live_service.py`가 "이 모듈은 실거래 자금 경로"라고 모듈 docstring에서 명시하고, 3중 방어를 순수 함수로 분리해 유닛테스트 가능하게 만들었다: (1) `_risk_gate_orders` — 견적가 없으면 무조건 거부(`no_quote`), 매수/매도 모두 `min(요청수량, cap_usd/price, MAX_ORDER_SHARES)`로 클램프, 공매도 금지(보유 없으면 매도 거부), 최소 수량 미만 거부; (2) `_review_blocks_order` — 브로커의 사전심사(`review_equity_order`) 응답에서 거부 신호가 있으면 실제 주문 전송을 차단; (3) `execute_enabled()`(`ROBINHOOD_EXECUTE` env) + `dry_run` 이중 킬스위치. 기본 주문당 명목 한도는 `DEFAULT_MAX_ORDER_USD = 25.0`. 전 과정이 `AUDIT_DIR`(`dashboard/storage/audit/robinhood`)에 단계별로 감사 로깅됨.
- **서드파티 코드 격리**: `runtime_type: ai_hedge_fund`(virattt/ai-hedge-fund) 실행은 `infrastructure/ai_hedge_fund/adapter.py`에서 **서브프로세스로 격리**하고, 전달 환경변수를 화이트리스트로 제한한다:
```python
_SUBPROCESS_ENV_KEYS = frozenset({
    "PATH", "TMPDIR", "LANG", "LC_ALL", "TZ",
    "SSL_CERT_FILE", ..., "FINANCIAL_DATASETS_API_KEY", "OPENROUTER_API_KEY",
})
```
(`adapter.py:52`) — ATL DB URL, 다른 프로바이더 키 등은 서브프로세스 경계를 넘지 않음. docstring: "It never executes or mutates trades."(모든 매매 실행은 ATL 코어로 되돌아온다).

### 5. 기타 특기사항

- `orchestration/FinAgents/`는 대시보드와 독립적인 대규모 멀티에이전트 리서치 프레임워크로 alpha/risk/portfolio/execution/data agent pool을 MCP로 연결하고, `orchestrator/core/`에 `dag_planner.py`(DAG 기반 태스크 플래닝), `rl_policy_engine.py`(RL 정책), `sandbox_environment.py`(전략/시나리오 시뮬레이션 샌드박스 — 프로세스 격리가 아니라 백테스트 시나리오 격리를 의미), `agent_pool_monitor.py`(에이전트 풀 헬스체크)를 포함한다. 성숙도는 대시보드 경로보다 낮아 보이며(중복/복구 파일 `finagent_orchestrator_recovered.py` 존재), 두 서브시스템 간 통합 정도는 제한적이다.
- 리더보드(`dashboard/backend/domain/leaderboard/`)가 LLM 에이전트 전략을 baseline(buy-hold, equal-weight 등)과 나란히 백테스트해 비교하는 평가 하네스 역할을 겸함 — `strategies/llm_agent.py`가 "모든 모델은 `model_id`만 다른 config 엔트리"라는 설계로 모델 비교를 표준화.

### AIOS 시사점

- **채택할 패턴**: (1) 에이전트 설정과 자격증명/실행 파일 경로를 구조적으로 분리하는 원칙("agent config는 절대 credential을 담지 않는다")은 AIOS 에이전트 스키마 설계의 최우선 원칙으로 그대로 채택 가능. (2) 라이브 주문 경로의 3중 게이트(순수 함수 리스크 게이트 + 브로커 사전심사 반영 + env 킬스위치)와 전체 감사 로깅은 institutional-grade 실행 게이트웨이의 참조 구현으로 손색없음. (3) 서드파티 전략 코드를 서브프로세스+환경변수 화이트리스트로 격리하는 방식은 "마켓플레이스에서 받은 코드를 어떻게 안전하게 돌릴 것인가"에 대한 최소 기준선으로 삼을 만함.
- **더 강화해야 할 부분**: (1) 마켓플레이스에 서명/출처 검증이 전혀 없다 — AIOS는 최소한 템플릿 해시 서명, 발행자 신원 검증, 프롬프트 콘텐츠에 대한 정적 스캔(민감 지시어, 프롬프트 인젝션 패턴)을 요구해야 한다. (2) 메인 백테스트 엔진에 진짜 의미의 walk-forward(파라미터 재적합 포함)와 다양한 리스크 지표(Sortino/Calmar/VaR)가 결여되어 있어, 기관용으로는 FinAgents 쪽 로직을 대시보드 코어로 승격하거나 AIOS 자체 검증 엔진에 이식해야 한다. (3) `runtime_type: pipeline`(대다수 마켓플레이스 에이전트)은 순수 프롬프트라서 실행 안전성은 높지만 전략 표현력이 제한적 — AIOS의 Strategy IR은 OBaI 스타일의 구조화 스키마(§Part B-1)와 결합하는 편이 검증 가능성 면에서 더 유리하다.

---

## Part B. OBaI

### 메타데이터

| 항목 | 값 |
|---|---|
| License | **Apache License 2.0 + "Commons Clause" License Condition v1.0** — Apache 2.0 조건에 더해 "Sell"(호스팅/컨설팅 등 기능 가치에서 유래한 유상 서비스 제공)을 금지. 저작권자: Sujeeth Shetty. 기관에서 SaaS로 재판매/호스팅하려면 라이선스 재협상 필요 |
| 언어 | Python (마이크로서비스형 MCP 서버 다수 + 코어 에이전트 오케스트레이터) |
| 규모 | Python 파일 441개, 약 115,000 LOC, 저장소 전체 17MB |
| 최근 커밋 | `1b34e53` — "Merge pull request #88 .../release/1.6.0", 2026-08-21 |

구조: `src/obai/core_agents/`(Central Hub + 10개 전문 에이전트, agents-as-tools 패턴), `src/{backtest,crypto,events-news,fundamentals,market-data,options,portfolio,prediction-markets,research,screening}-server/`(각 도메인별 독립 MCP 서버, streamable-http), `skills/`(Claude Skills로 노출되는 CLI 워크플로 — `autotrader`가 유일하게 실거래를 다룸).

### 1. Strategy JSON/schema

정의: `src/backtest-server/src/models/strategy.py` (`StrategyDefinition` dataclass, `from_dict`/`to_dict`/`validate` 내장). 이것이 사실상 "Strategy IR" 후보다.

```python
@dataclass
class StrategyDefinition:
    name: str
    universe: Universe                 # symbols[], benchmark
    data_config: DataConfig            # start_date, end_date, train_end_date, timeframe
    indicators: list[IndicatorConfig]  # id, type, params, source
    entry_rules: RuleSet               # logic(AND/OR), conditions[]
    exit_rules: RuleSet
    position_sizing: PositionSizing = PositionSizing()   # method, max_position_pct, max_positions, allocation_mode
    risk_management: RiskManagement = RiskManagement()   # stop_loss_pct, take_profit_pct, close_eod, no_entry_after
    execution_config: ExecutionConfig = ExecutionConfig()  # slippage_pct, commission_pct, initial_capital, ...
```

조건(Condition)은 `Operand`(indicator|constant|time_of_day|time 중 정확히 1개) 두 개와 `operator`(`greater_than`, `less_than`, `crosses_above`, `crosses_below`, `equals`, `not_equals`, `after_time`, `before_time`)로 구성된다. 지원 지표는 SMA/EMA/RSI/MACD/BBANDS/ATR/ADX/STOCH 등 표준 TA-Lib 계열 20여 종 + 캔들패턴 60여 종 + VWAP(intraday 전용). `validate()`가 스키마 자체 내에서 상당히 엄격하게 검증한다: 미정의 지표 참조 차단(`_validate_rule_refs`), 포트폴리오 모드는 daily 타임프레임 강제, VWAP은 daily에서 거부, 타임프레임별 최대 조회 기간 제한(`TIMEFRAME_MAX_YEARS`: daily 30y/1시간 5y/15분·5분 2y), 유니버스 최대 250종목(`MAX_UNIVERSE_SIZE`, 이유: FMP 쿼터/메모리 보호).

체결 타이밍이 스키마에 상수로 박제되어 있다는 점이 특기할 만하다:
```python
# The engine evaluates conditions on a bar's close and fills at the next bar's
# open ... Naming it in the payload is what lets a later turn rule out look-ahead
FILL_TIMING = "signal_at_bar_close_fill_at_next_bar_open"
```
(`models/strategy.py:72`) — 결과 JSON에 항상 이 값을 동봉해 "이 백테스트가 룩어헤드 없이 실행되었다"는 것을 결과 자체가 자기 증명하도록 설계했다.

### 2. 반복 백테스트 루프(AI iterate → backtest → refine)

전체 로직은 코드가 아니라 **에이전트 시스템 프롬프트**(`src/obai/core_agents/prompts/strategy.md`, 621줄)에 있다. Mode 2("Agent-Designed Strategy")의 Iteration Protocol:

```
Iteration 1: Baseline        — 가장 단순한 가설 검증, train range에서 실행
Iteration 2: One meaningful improvement — 필터 1개 추가 후 비교
Iteration 3: Risk/exits refinement — 리스크 지표 악화시키는 변경은 기각
Iteration 4: Sensitivity     — 인접 파라미터 2~3개 변형 비교(backtest_compare_strategies_tool)
Iteration 5: Final validation — 전체 기간 재실행, train vs full-period degradation 명시
```
(`strategy.md:275`) 정지 조건은 "보통 3~5라운드"라는 가이드라인뿐, 코드로 강제되는 하드 스톱은 없음. 오버피팅 방어는 (a) 프롬프트가 "정직화된(parsimonious) 저자유도 전략을 선호하라"고 지시, (b) train/full-period 성과 저하(degradation)를 명시적으로 보고하도록 강제, (c) trade count가 적으면 statistical power 부족을 반드시 flag하도록 강제 — 그러나 이 모든 것이 **LLM의 준수 의지에 의존**하며, 백엔드에서 "저하가 임계치를 넘으면 verdict를 강제로 reject로 바꾼다" 같은 코드 레벨 강제는 발견되지 않았다.

### 3. OOS 검증

- **단순 train/test split**: `DataConfig.train_end_date`(없으면 전체 구간의 75% 지점을 자동 계산, `get_train_end()`)로 in-sample/out-of-sample을 나눈다.
- **Walk-forward**: `src/backtest-server/src/engine/walk_forward.py`. `generate_windows()`가 구간을 `n_windows+1`개로 나눠 **확장형(expanding)** 윈도우를 만들고(각 윈도우의 train은 처음부터 누적, test는 다음 세그먼트), 각 윈도우의 train/test 백테스트를 `asyncio.gather`로 병렬 실행한다. 집계 지표:

```python
mean_test_sharpe, std_test_sharpe, mean_test_win_rate, mean_test_max_drawdown,
consistency_score,  # % of windows where test Sharpe > 0
degradation,        # mean(train_sharpe - test_sharpe)
```
(`models/strategy.py` `WalkForwardResult`) 최소 데이터 요구량은 `n_windows`당 365일(`walk_forward.py:53`)로 하드코딩.

- **수용 게이트(acceptance gate)**: 코드가 아니라 프롬프트에 명시된 임계값이다.
```
Consistency score < 60% suggests overfitting.
Degradation > 0.5 indicates significant train/test decay.
```
(`prompts/strategy.md:316`) 최종 결과는 9섹션 "Output Contract"의 `Verdict`(`accept`|`paper_trade`|`needs_more_research`|`reject`)로 귀결되지만, 이 verdict를 매기는 것은 LLM 자신이지 백테스트 엔진이 아니다 — **게이트가 코드 레벨이 아니라 프롬프트 레벨**이라는 점이 기관용으로는 핵심 리스크다.

### 4. MCP 노출

각 도메인이 독립 FastMCP 서버로 분리되어 있고(`src/backtest-server/src/server.py` 등), **비즈니스 로직 자체가 MCP 서버 안에 있다** — 즉 "API 뒤에 MCP가 얇게 올라간 구조"가 아니라 "MCP 서버가 곧 서비스"다. 예: `backtest-server`는 8개 도구를 등록한다.
```python
mcp = FastMCP("backtest-server", version=__version__)
@mcp.tool(...) async def backtest_run_strategy_tool(...)
@mcp.tool(...) async def backtest_get_job_status_tool(...)
@mcp.tool(...) async def backtest_get_supported_indicators_tool()
@mcp.tool(...) async def backtest_download_data_tool(...)
@mcp.tool(...) async def backtest_list_available_data_tool(...)
@mcp.tool(...) async def backtest_manage_storage_tool(...)   # 파괴적 작업 포함
@mcp.tool(...) async def backtest_get_trade_log_tool(...)
@mcp.tool(...) async def backtest_compare_strategies_tool(...)
@mcp.tool(...) async def backtest_clear_cache_tool(...)
@mcp.tool(...) async def backtest_walk_forward_tool(...)
```
(`src/backtest-server/src/server.py`) 실제 백테스트 실행(`_execute_strategy`), 워밍업 지표 계산, 벤치마크 리졸브 등 핵심 엔진 코드가 전부 이 서버 모듈 안에 물리적으로 존재한다.

- **인증**: 일반 MCP 도구 호출에는 Bearer/API-key 인증이 없다(`docker-compose.yml`은 각 서버를 `HOST=0.0.0.0`으로 포트 바인딩, 외부 데이터 프로바이더 키만 컨테이너 env로 주입). 유일한 예외는 파괴적 스토리지 정리 도구로, 운영자 전용 `confirm_token`을 `BACKTEST_STORAGE_ADMIN_TOKEN`과 대조한다(`server.py:431`). 즉 신뢰 경계는 "이 MCP 엔드포인트에 네트워크로 접근 가능한 자는 모두 신뢰한다"는 전제 위에 있고, 이는 로컬/사설망 배포를 가정한 설계로 보인다.

### 5. 데이터/브로커 통합 및 라이브 트레이딩

- 리서치 측(Hub/전문 에이전트)은 명시적으로 **read-only**다. `skills/autotrader/SKILL.md`: "OBaI is **read-only** — never ask it to place trades or manage positions."
- 실행은 완전히 분리된 `skills/autotrader/` 스킬이 담당하며, 브로커는 **Alpaca Paper Trading으로 하드코딩**되어 있다:
```python
"""Typed wrapper around Alpaca TradingClient for paper trading."""
...
paper=True,  # ALWAYS paper — hard-coded
```
(`skills/autotrader/lib/alpaca_client.py:80,98`) — 코드베이스 전체에서 라이브 브로커 엔드포인트나 라이브 전환 플래그를 찾을 수 없었다. 즉 OBaI는 설계상 라이브 트레이딩이 **존재하지 않는다**(AgenticTrading과 대비되는 지점).
- 실행 전 리스크 검증은 코드 레벨로 강제된다(`skills/autotrader/lib/risk.py`, `RiskChecker`): `MAX_POSITION_PCT`(기본 10%), `MAX_DAILY_TRADES`(기본 20), `MAX_DAILY_LOSS_PCT`(기본 3%), `MAX_EXPOSURE_PCT`(기본 90%) — 모두 매 체크마다 Alpaca API에서 직접 상태를 조회해 재계산하는 stateless 설계("서버 재시작해도 카운터가 리셋되지 않음"). 즉 여기서는 §2/§3의 "전략 채택" 게이트와 달리, **주문 집행 게이트는 코드로 강제**되어 있다 — OBaI 내에서도 "전략 판단"과 "주문 집행"의 신뢰 수준이 다르게 설계된 셈이다.
- 데이터: FMP(펀더멘털/시세/스크리닝/백테스트 OHLCV, 유료 ~$19/mo)가 백본, Massive.com(옵션 체인/Greeks), Tavily(뉴스), Exa(정성 리서치), Polymarket(예측시장, 공개 API), Coinbase Advanced Trade(현물 크립토, 공개 API·키 불필요).

### AIOS 시사점

- **채택할 패턴**: (1) `StrategyDefinition` dataclass는 그대로 AIOS Strategy IR의 출발점으로 쓸 만큼 완성도가 높다 — 특히 `FILL_TIMING` 상수를 결과에 동봉해 룩어헤드 부재를 결과 자체가 자기 증명하게 만드는 방식, 그리고 지표/규칙 참조를 스키마 `validate()` 단계에서 정적으로 검증(미정의 참조·타임프레임 불일치·유니버스 크기 등)하는 방식은 그대로 이식 가치가 있다. (2) "리서치(read-only) 에이전트"와 "집행(execution) 스킬"을 프로세스/권한 수준에서 완전히 분리하고, 집행 쪽에만 코드 레벨 하드 리스크 리밋(포지션/일일 손실/노출 상한)을 두는 이원 구조는 AIOS의 권한 모델에 바로 적용 가능. (3) walk-forward 결과에 `execution_config`/`strategy`/`fill_timing`/`warmup_bars`를 모두 동봉해 "재현 가능한 self-describing 아티팩트"로 만드는 방식은 감사 추적성 측면에서 유용.
- **더 강화해야 할 부분**: (1) 전략 채택 게이트(consistency_score < 60%, degradation > 0.5)가 시스템 프롬프트 문자열로만 존재하고 백엔드가 이를 강제하지 않는다 — 기관용 AIOS는 이 임계값을 백테스트 서버(코드)로 끌어내려 `Verdict`를 서버가 계산하고 LLM은 그 결과를 서술만 하도록 바꿔야 한다(prompt-enforced governance → code-enforced governance). (2) MCP 서버들에 인증이 없다 — 프로덕션에서는 mTLS/서비스 토큰 등 MCP 트랜스포트 레벨 인증이 필수이며, 비즈니스 로직이 MCP 서버에 직접 내장된 구조이므로 이 서버가 곧 신뢰 경계임을 명확히 인지하고 설계해야 한다. (3) 현재 라이브 트레이딩이 아예 없는 것은 안전하지만, 향후 라이브를 붙일 때는 AgenticTrading의 3중 게이트(§Part A-4)를 참조 모델로 이식하는 편이 바람직하다. (4) Commons Clause 라이선스로 인해 이 코드를 그대로 흡수해 서비스로 재판매하는 것은 법적으로 제한되므로, 아키텍처/스키마 아이디어 차용에 그치고 코드 재사용 시 라이선스 검토 필요.

---

## 부록: 두 프로젝트 비교 요약

| 항목 | AgenticTrading | OBaI |
|---|---|---|
| 라이선스 | OpenMDW 1.0 | Apache 2.0 + Commons Clause |
| 규모 | ~278k LOC / 877 files / 81MB | ~115k LOC / 441 files / 17MB |
| 아키텍처 중심 | 웹 대시보드(에이전트 마켓플레이스+백테스트+페이퍼/라이브) + 별도 FinAgents 리서치 프레임워크 | 도메인별 MCP 서버 집합 + Central Hub(agents-as-tools) |
| Strategy 표현 | 자유 텍스트 프롬프트 파이프라인(+옵션 호스티드 런타임) | 구조화 JSON IR(dataclass, 자체 validate) |
| 라이브 트레이딩 | 있음(Robinhood MCP, OAuth, `live_trading_enabled` 플래그 + 3중 리스크 게이트) | 없음(Alpaca paper 하드코딩) |
| 전략 채택 게이트 | 없음(백테스트 지표만 표시) | 있으나 프롬프트 레벨(코드 강제 아님) |
| Walk-forward | FinAgents 쪽에 있으나 재적합 없는 구간 분해 수준 | backtest-server에 확장형 walk-forward + consistency/degradation 지표 내장 |
| MCP 인증 | 브로커 MCP(Robinhood)는 OAuth; 내부는 별도 확인 범위 밖 | 없음(파괴적 도구 1개만 admin token) |
| 마켓플레이스 서명/검증 | 없음(코드 주석이 명시적으로 인정) | 해당 없음(마켓플레이스 개념 자체가 없음) |
