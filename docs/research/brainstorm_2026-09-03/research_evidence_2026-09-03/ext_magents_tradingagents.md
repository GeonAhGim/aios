# 외부 레퍼런스 분석: Magents / TradingAgents (organizational multi-agent 패러다임)

조사일: 2026-09-03
조사 방식: 코드 레벨 read-only 분석 (shallow clone, `--depth 1`)
클론 위치: 로컬 scratchpad (`.../scratchpad/ext3/Magents`, `.../scratchpad/ext3/TradingAgents`) — repo 자체에는 어떤 변경도 가하지 않음.

목적: AIOS Strategy Factory Plane(NL intent → canonical Strategy IR)의 현재 두 authoring 경로(non-AI goal wizard, 스텁 상태의 NL LLM prompt 경로) 모두 debate/역할 분리형 multi-agent 구조를 쓰지 않는다. 이 문서는 "조직형 multi-agent(analyst-researcher-trader-risk 역할 분리 + 논쟁)"라는 세 번째 패러다임이 실제로 어떻게 구현되는지, 그리고 AIOS가 이를 참고할 때 Strategy Factory Plane의 경계 밖(Policy/Execution Plane)으로 침범하는 설계가 섞여 있지는 않은지를 코드로 확인한다.

---

## 0. 기본 건강성 신호 (Health Signals) 비교

| 항목 | TauricResearch/TradingAgents | LLMQuant/Magents |
|---|---|---|
| GitHub stars | **102,351** | 66 |
| forks | 19,698 | 17 |
| open issues | 365 | 0 |
| 생성일 | 2024-12-28 | 2025-03-11 |
| 최근 push | 2026-09-01 (조사 시점 기준 이틀 전, 매일 다수 커밋) | 2026-05-30 |
| contributors (API 응답) | 19+ (활발한 외부 PR 병합 이력, CHANGELOG 28KB) | 2 |
| license | Apache-2.0 | MIT |
| 테스트 | `tests/` 62개 파일, pytest 기반, CI 존재 | `tests/` 5개 파일 (config/event/order/portfolio/risk) |
| 논문/레퍼런스 | README에 arXiv:2412.20138 배지 + 후속 Trading-R1 기술 리포트(arXiv:2509.11420) 명시, `## Citation` 섹션 별도 존재 | 논문/기술 리포트 없음 |
| 면책 조항 | README: "designed for research purposes... not intended as financial, investment, or trading advice" (링크: tauric.ai/disclaimer) | 명시적 면책 조항 없음(단, README에 "hedge fund **simulation** and backtesting"로 명확히 시뮬레이션 프레임임을 표기) |

**결론**: TauricResearch/TradingAgents는 과제에서 우려한 "저품질 개인 클론"이 전혀 아니다. arXiv 논문 기반, 10만+ star, 거의 매일 커밋되는 활성 프로젝트이며 CLI, Docker, LangGraph 기반 checkpoint/resume, 다중 LLM 벤더 어댑터(OpenAI/Anthropic/Google/Bedrock/Azure/DeepSeek/OpenRouter/Ollama 등)까지 갖춘 실질적 엔지니어링 성숙도를 보인다. Magents는 반대로 개인/소규모 프로젝트(contributor 2명, star 66)로, 코드 품질은 준수하나 커뮤니티 검증 수준은 낮다. 두 프로젝트를 "같은 신뢰도"로 취급해서는 안 된다.

---

## Part A. TauricResearch/TradingAgents

### A.1 Agent role taxonomy — 역할당 실제로 다른 프롬프트/도구를 쓰는가

디렉토리 구조 자체가 역할 분리를 보여준다 (`tradingagents/agents/`):

```
analysts/   fundamentals_analyst.py, market_analyst.py, news_analyst.py,
            sentiment_analyst.py, social_media_analyst.py
researchers/ bull_researcher.py, bear_researcher.py
managers/   research_manager.py, portfolio_manager.py
risk_mgmt/  aggressive_debator.py, conservative_debator.py, neutral_debator.py
trader/     trader.py
```

총 **11개의 구별된 agent 노드**(analyst 5 + researcher 2 + manager 2 + risk debator 3 + trader 1, 실제로는 12)가 LangGraph 노드로 등록되며, 이들은 코스메틱 네이밍이 아니라 실제로 서로 다른 시스템 프롬프트, 서로 다른 tool 세트, 서로 다른 state 필드를 사용한다.

예: Market Analyst는 기술적 지표 전용 tool을 바인딩한다 (`tradingagents/agents/analysts/market_analyst.py:18-22`):
```python
tools = [
    get_stock_data,
    get_indicators,
    get_verified_market_snapshot,
]
```
반면 Bull/Bear Researcher는 별도 tool 없이(`NO_EXTERNAL_TOOLS`) 4개 analyst 리포트(market/sentiment/news/fundamentals)를 입력으로만 받아 논쟁 텍스트를 생성한다. 즉 "데이터 수집(analyst) → 해석/논쟁(researcher) → 계획 종합(manager) → 주문 초안(trader) → 리스크 검토(risk debator) → 최종 승인(portfolio manager)"의 파이프라인이 코드 구조로 강제되어 있다.

Bull Researcher 프롬프트 발췌 (`tradingagents/agents/researchers/bull_researcher.py:30-47`):
```
You are a Bull Analyst advocating for investing in the {target_label}. Your task is to build a strong, evidence-based case emphasizing growth potential, competitive advantages, and positive market indicators.
...
- Bear Counterpoints: Critically analyze the bear argument with specific data and sound reasoning, addressing concerns thoroughly and showing why the bull perspective holds stronger merit.
```
Bear Researcher는 대칭적으로 정반대 지시를 받는다 (`tradingagents/agents/researchers/bear_researcher.py:30-38`, 발췌):
```
You are a Bear Analyst making the case against investing in the {target_label}. Your goal is to present a well-reasoned argument emphasizing risks, challenges, and negative indicators.
```
Risk 팀의 3-way debate 역시 Aggressive/Conservative/Neutral로 실제 성향이 다른 프롬프트를 사용한다 (`tradingagents/agents/risk_mgmt/aggressive_debator.py:29`):
```
As the Aggressive Risk Analyst, your role is to actively champion high-reward, high-risk opportunities, emphasizing bold strategies and competitive advantages.
```

**판정**: 코스메틱 네이밍이 아니라 실제 역할별 프롬프트/자료 분리이며, 11개 이상의 뚜렷이 구별되는 agent 노드로 구성된 진짜 조직형 구조다.

### A.2 Debate 메커니즘 — 구조화된 논쟁 + 중재자 + 감사 가능한 transcript?

Debate는 두 레이어에 존재한다.

1. **Researcher 레이어 (Bull vs Bear)**: `investment_debate_state`(TypedDict, `tradingagents/agents/utils/agent_states.py:8-18`)에 `bull_history`, `bear_history`, `history`, `current_response`, `count`를 유지하며 매 턴마다 상대방의 마지막 argument를 프롬프트에 주입한다 (`opponent_argument_or_opening` 헬퍼).
2. **Risk 레이어 (Aggressive vs Conservative vs Neutral)**: `risk_debate_state`가 동일한 패턴으로 3자 논쟁을 관리한다.

논쟁 종료 조건은 **결정론적 라운드 카운터**이며 합의(consensus) 기반이 아니다 (`tradingagents/graph/conditional_logic.py:52-73`):
```python
def should_continue_debate(self, state: AgentState) -> str:
    if (
        state["investment_debate_state"]["count"] >= 2 * self.max_debate_rounds
    ):  # 3 rounds of back-and-forth between 2 agents
        return "Research Manager"
    if state["investment_debate_state"]["current_response"].startswith("Bull"):
        return "Bear Researcher"
    return "Bull Researcher"
```
`should_continue_risk_analysis`도 동일한 패턴(3배수 카운터, 발언자 순환)으로 구현되어 있다. `default_config.py`의 기본값은 `max_debate_rounds=1`(즉 Bull→Bear→Research Manager로 총 2턴), `max_risk_discuss_rounds=1`이지만 설정으로 라운드 수를 늘릴 수 있다.

**중재자(arbiter)는 별도 agent다**: Research Manager가 bull/bear 논쟁 history 전체를 받아 5단계 rating(Buy/Overweight/Hold/Underweight/Sell)으로 판정한다 (`tradingagents/agents/managers/research_manager.py:26-46`, 발췌):
```
As the Research Manager and debate facilitator, your role is to critically evaluate this round of debate and deliver a clear, actionable investment plan for the trader.
...
Commit to a directional stance only when the debate's strongest arguments clearly warrant one. Choose Hold when the evidence is balanced, materially conflicting, ambiguous, or insufficient...
```
risk 논쟁 쪽은 Portfolio Manager가 동일 역할(중재자)을 수행한다 (`tradingagents/agents/managers/portfolio_manager.py`).

**Transcript 보존**: 논쟁 전체 history(bull_history/bear_history/aggressive_history/conservative_history/neutral_history 포함)는 `trading_graph.py`의 `save_reports()`를 통해 디스크에 마크다운으로 영속화된다 (`tradingagents/graph/trading_graph.py:494-507, 585-602`):
```python
def save_reports(self, final_state, ticker, save_path=None) -> Path:
    """Write the markdown report tree for a completed run, like the CLI does.
    Programmatic callers get the same on-disk reports the CLI produces. ..."""
    ...
    return write_report_tree(final_state, ticker, save_path)
```
`_process_events`나 `test_reporting.py`, `test_memory_log.py` 등 테스트가 이 저장 경로를 커버한다.

**판정**: 진짜 구조화된 논쟁 + 별도 arbiter agent + 결정론적 종료 규칙 + 감사용 transcript 영속화, 4개 항목 모두 확인됨. 단, 종료 조건이 "합의 도달"이 아니라 "고정 라운드 수"라는 점은 명확히 해 둘 필요가 있다 — 논쟁의 질이 아니라 횟수로 종료된다.

### A.3 최종 트레이딩 결정 생성 — 결정론적 집계 규칙인가, 한 agent의 판단인가

파이프라인은 **연쇄적 단일-판단자 구조**다: 데이터/논쟁을 종합하는 "집계 함수"는 없고, 각 단계마다 정해진 한 agent가 이전 단계 산출물을 입력받아 다음 산출물을 만든다.

1. Research Manager → `investment_plan` (5단계 rating: Buy/Overweight/Hold/Underweight/Sell)
2. Trader → `trader_investment_plan` (3단계 action: Buy/Sell/Hold + entry/stop-loss/position sizing)
3. Risk 3-way debate (트레이더 계획을 두고 논쟁)
4. Portfolio Manager → `final_trade_decision` (최종 5단계 rating, 승인/거부 권한)

Trader는 Pydantic 구조화 출력(`TraderProposal`)을 사용하며 stop-loss/entry price/position sizing 필드를 가진다 (`tradingagents/agents/trader/trader.py:9, 69-75`; 필드 정의는 `tradingagents/agents/schemas.py:145-154`):
```python
stop_loss: float | None = Field(
    default=None,
    description="Optional stop-loss price in the instrument's quote currency.",
)
position_sizing: str | None = Field(
    default=None, ...
)
```
Portfolio Manager가 최종 게이트다 (README, 발췌):
```
The Portfolio Manager approves/rejects the transaction proposal. If approved, the order will be sent to the simulated exchange and executed.
```
최종 rating 추출은 LLM 재판단이 아니라 **결정론적 문자열 파싱**이다 (`tradingagents/graph/signal_processing.py:1-38`, 발췌):
```python
class SignalProcessor:
    """Read the 5-tier rating out of a Portfolio Manager decision."""
    def process_signal(self, full_signal: str) -> str:
        rating = extract_rating(full_signal)
        return rating if rating is not None else RATING_REVIEW
```
파싱 실패 시 임의로 "Hold"로 대체하지 않고 `REVIEW`(사람 검토 필요)를 반환하도록 명시(`#1170` 이슈 참조 주석 포함) — fail-safe 설계가 되어 있다는 점은 AIOS의 governance 관점에서 참고할 만하다.

**판정**: 정량적 가중합/투표 같은 "집계 알고리즘"은 없다. 대신 역할별로 한 명의 LLM judge가 순차적으로 이전 단계 산출물을 좁혀나가는 funnel 구조이며, 마지막 두 단계(Trader→Portfolio Manager)가 사실상의 checks-and-balances 역할을 한다. "portfolio-of-agents가 투표로 결정"하는 방식이 아니라 "역할 위계(hierarchy)의 최종 승인권자가 결정"하는 방식이다.

### A.4 실행/리스크 한계 접근 여부 — 순수 연구/시그널 생성 프레임워크인가

코드베이스 전체를 `broker`, `alpaca`, `ib_insync`, `place_order`, `submit_order` 등 키워드로 검색한 결과, 실제 브로커/거래소 연동 코드는 전혀 없다(데이터 조회용 `tradingagents/dataflows/*.py`에서 무관한 문자열 매치만 발생). README가 이를 명시적으로 확인해 준다:

> "If approved, the order will be sent to the **simulated exchange** and executed." (README.md:91)
>
> "TradingAgents framework is designed for **research purposes**... [It is not intended as financial, investment, or trading advice.]" (README.md:61)

리스크 관련 개념(`stop_loss`, `position_sizing`, `risk`)은 존재하지만, 이는 모두 **LLM이 자연어/구조화 필드로 "제안"하는 값**이지 실제로 계좌 잔고나 익스포저를 조회해서 강제하는 하드 리밋 엔진이 아니다. `default_config.py`에 리스크 관련 설정은 `max_debate_rounds`, `max_risk_discuss_rounds`뿐이며, VaR/max drawdown/position limit 등 정량적 리스크 한도 체계는 존재하지 않는다.

**판정**: TradingAgents는 순수하게 "시그널 생성 + 논쟁 기반 심사"를 하는 리서치 프레임워크다. 실제 주문 실행, 브로커 연동, 계좌 단위 하드 리스크 리밋은 스코프 밖이며 "simulated exchange"라는 표현이 이를 자인한다. AIOS 관점에서 이 패턴을 도입해도 **Execution Plane이나 Policy Plane(계좌 레벨 하드 리밋, 실제 주문 라우팅)에는 전혀 닿지 않는다** — Strategy Factory Plane 내부 로직으로 완전히 격리 가능하다.

### A.5 AIOS 시사점

- AIOS의 두 번째 authoring 경로(스텁 NL LLM prompt)를 확장할 때, TradingAgents의 "analyst(데이터 해석) → researcher debate(bull/bear) → manager(중재) → trader(집행안 초안) → risk debate(3-way) → portfolio manager(최종 승인)" 6단계 파이프라인은 **하나의 참고 아키텍처**로 쓸 수 있다. 특히 (a) 역할별 프롬프트/자료 분리, (b) 고정 라운드 논쟁 후 별도 arbiter agent의 구조화 출력, (c) 파싱 실패 시 "Hold로 눙치지 않고 REVIEW로 명시"하는 fail-safe 패턴은 AIOS의 Strategy IR 생성 신뢰성 확보에 바로 적용 가능하다.
- 다만 이 패턴 전체는 **Strategy Factory Plane 내부**(canonical Strategy IR을 만들어내는 authoring 로직)에만 해당하고, AIOS의 기존 Policy/Execution Plane(실제 주문 라우팅, 계좌 레벨 하드 리스크 리밋)과는 완전히 분리된 채로 이식해야 한다 — TradingAgents 자체가 "simulated exchange"까지만 다루고 실제 브로커 연동이 없다는 사실이 이 경계를 코드 레벨에서 뒷받침한다. Strategy IR 산출물(Trader의 `entry_price`/`stop_loss`/`position_sizing` 제안, Portfolio Manager의 최종 rating)은 AIOS Strategy IR의 "제안된 파라미터"로만 취급되어야 하며, AIOS의 기존 Policy Plane 하드 리밋을 재검증 없이 그대로 실행에 통과시켜서는 안 된다.
- Multi-round debate는 LLM 호출 비용이 선형으로 증가한다(라운드당 최소 2~3회 호출 × 여러 노드). AIOS가 이 패턴을 채택할 경우 "AI 크레딧 스텁 해제" 시점의 비용 예산과 직결되는 설계 변수로 별도 관리 필요.

---

## Part B. LLMQuant/Magents

### B.1 "Pod"란 무엇인가 — agent pod 간 격리 경계(프로세스/설정/상태)

README 자체가 격리 수준을 명확히 규정한다 (`README.md:9`):

> "Magents models independent strategy as **concurrent agents within a shared trading simulation**, enabling realistic backtesting under **unified data feeds and risk controls**."

코드도 이를 그대로 구현한다. `BasePod`/`MultiAgentPod`(`src/pods/base.py:11-27, 164-180`)는 프로세스나 컨테이너가 아니라 **같은 Python 프로세스 내의 클래스 인스턴스**이며, 하나의 공유 `event_queue`(엔진이 주입)를 통해서만 통신한다:
```python
class BasePod(ABC):
    def __init__(self, pod_id: str, instruments: List[str]):
        self.pod_id = pod_id
        self.instruments = instruments
        self.logger = logging.getLogger(f"pod.{pod_id}")
        self.event_queue = None  # Set by the engine when registering the pod
```
`BacktestingEngine`(`src/core/engine.py:16-63`)은 모든 pod를 하나의 dict(`self.pods`)에 등록하고, 시장 데이터/주문/체결/리스크 이벤트를 **동일한 이벤트 루프**에서 모든 pod에 순차 브로드캐스트한다 (`_process_market_data_event`, `_process_order_event` 등). 즉:

- **프로세스 격리 없음**: 모든 pod가 같은 스레드/프로세스에서 동기 실행되며, 한 pod의 예외는 `try/except`로 로깅만 하고 다른 pod 실행을 막지 않는 정도의 방어만 있다 (`src/pods/base.py:186-190` 등에서 개별 pod별 try/except).
- **설정 격리는 부분적**: `StrategyFactory.create_strategy()`가 pod별 `strategy_config`(전략 타입별 파라미터: `fast_window`, `signal_threshold`, `position_size` 등)를 분리 적용하지만, 이는 단순 딕셔너리 오버라이드이며 별도 sandbox/네임스페이스가 아니다 (`src/pods/strategies/factory.py:118-174`).
- **리스크 관리는 중앙집중**: `RiskManager`(`src/risk/manager.py:14-26, 41-60`)가 pod별 한도(`risk_limits[pod_id]`)와 전역 한도(`global_limits`)를 모두 가지고 있는 **단일 객체**이며, 모든 pod의 주문이 이 하나의 `validate_order()`를 통과해야 한다:
```python
class RiskManager:
    """Central risk management system that enforces risk constraints
    and monitors portfolio risk metrics."""
    def __init__(self):
        self.risk_limits: Dict[str, List[RiskLimit]] = {}  # Pod-specific limits
        self.global_limits: List[RiskLimit] = []  # Global limits
```
- **상태(포지션/자본)만 pod 단위로 분리**: `PortfolioManager`가 pod마다 별도 `Portfolio` 객체를 갖지만(`create_portfolio`), 이 역시 같은 프로세스 메모리 안의 dict 엔트리일 뿐이다.

**판정**: "pod"는 컨테이너/프로세스/VM 수준의 isolation 단위가 아니라, **단일 프로세스 내 논리적 그룹핑(전략별 agent 묶음 + 전용 Portfolio 객체)**에 가깝다. 격리되는 것은 (a) 포지션/PnL 장부(Portfolio), (b) 전략 파라미터 설정뿐이고, 실행 스레드·리스크 심사 엔진·이벤트 버스는 모두 공유된다. AIOS가 "agent pod = 강한 격리 경계"라는 그래프 A 등급 태그의 뉘앙스로 이 리포를 참고한다면, 실제로는 "논리적 pod, 물리적 단일 프로세스"라는 차이를 반드시 인지해야 한다.

### B.2 Portfolio-of-agents 오케스트레이션 — 자본 배분 방식

자본 배분은 **정적, 균등 분할**이며 성과 기반 재배분(rebalancing) 로직이 코드에 없다. `BacktestingEngine.register_pod()`가 등록 시점에 남은 자본을 pod 개수로 단순 나눈다 (`src/core/engine.py:58-63`):
```python
def register_pod(self, pod_id: str, pod_instance: Any) -> None:
    """Register a trading pod with the engine."""
    self.pods[pod_id] = pod_instance
    # Create a portfolio for this pod
    self.portfolio_manager.create_portfolio(pod_id, self.initial_capital / len(self.pods))
    self.logger.info(f"Registered pod: {pod_id}")
```
주의할 버그성 설계: `len(self.pods)`는 **현재까지 등록된 pod 수를 매번 다시 나누는 것**이 아니라 이 호출 시점의 값이므로, pod를 하나씩 순차 등록하면 먼저 등록된 pod가 더 많은 초기 자본을 배정받는 순서 의존적(order-dependent) 결함이 있다(예: pod 3개를 순서대로 등록하면 1/1, 1/2, 1/3로 배분되어 합이 initial_capital을 초과). 코드 검토 결과 이 값을 등록 후 재조정하는 로직은 없다.

통합 포트폴리오 가치와 pod별 배분 비율은 사후 집계일 뿐, 사전 최적화 배분이 아니다 (`src/core/portfolio.py:247-260`):
```python
def get_total_fund_value(self) -> float:
    """Get total value of all portfolios combined."""
    return self.combined_portfolio.total_value()

def get_pod_allocations(self) -> Dict[str, float]:
    """Get allocation percentage per pod."""
    total_value = self.get_total_fund_value()
    ...
    return {
        pod_id: portfolio.total_value() / total_value
        for pod_id, portfolio in self.portfolios.items()
    }
```
리스크 이벤트에 대한 대응도 pod 단위 하드코딩 규칙이다 — drawdown 한도 위반 시 해당 pod의 전 포지션을 강제 청산, position 한도 위반 시 50% 감축 (`src/core/engine.py:337-415`, `_handle_drawdown_breach`/`_handle_position_limit_breach`). 이는 "여러 전략 agent의 시그널을 하나의 포트폴리오 결정으로 종합"하는 최적화 로직(예: 상관관계 기반 리스크 패리티, Kelly 배분, mean-variance 최적화)이 아니라, **사후 규칙 기반 리스크 억제**에 가깝다.

**판정**: "portfolio-of-agents"는 실질적으로 (a) 등록 시점 균등분할(게다가 순서 의존 버그 있음), (b) 사후 집계(합산)로 combined value 표시, (c) 개별 pod 단위 하드 리밋 위반 시 규칙 기반 청산, 이 세 가지의 조합이다. 여러 전략의 시그널을 놓고 동적으로 자본을 재배분하는 "진짜 포트폴리오 최적화 계층"은 존재하지 않는다.

### B.3 AIOS 시사점

- Magents가 보여주는 "여러 전략을 각각 독립된 agent 묶음(pod)으로 관리하고, 중앙 리스크 관리자가 모든 주문을 최종 게이트한다"는 **구조적 아이디어**(central risk gate, pod별 portfolio 장부 분리)는 AIOS의 Strategy Factory Plane에서 "여러 IR 후보를 생성하는 authoring worker" 단위로 참고할 여지가 있다. 그러나 이 프로젝트에서 실제 자본 배분 로직은 매우 단순(균등분할, 심지어 순서 의존 버그 존재)하므로, AIOS가 "capital allocation across agents"를 설계 근거로 이 레포를 인용하는 것은 근거가 약하다 — 참고할 것은 이름뿐인 "포트폴리오 최적화"가 아니라 "중앙 리스크 게이트 + pod별 장부 분리"라는 구조 패턴이다.
- Pod가 프로세스/컨테이너 격리가 아니라는 점은 AIOS 설계에서 중요하다. AIOS가 "strategy pod"라는 용어를 향후 채택한다면, Magents 사례처럼 논리적 그룹핑에 그칠지, 아니면 실제 프로세스/테넌시 격리(예: 별도 컨테이너, 별도 크레덴셜)까지 갈지는 AIOS가 별도로 설계해야 하는 부분이며 이 레포는 후자에 대한 참고가 되지 않는다.
- Magents 역시 브로커 연동이 전혀 없는 backtesting 전용 프레임워크다(`BacktestingEngine`, `OrderBook.update_market_data`로 체결을 시뮬레이션; `_calculate_commission`/`_calculate_slippage`도 모두 in-memory 모델). 따라서 TradingAgents와 마찬가지로 이 패턴을 AIOS에 들여오더라도 **Execution/Policy Plane에는 닿지 않으며 Strategy Factory Plane 내부(혹은 그 검증용 backtest 하네스)로 국한**된다.

---

## 종합: 두 레포 모두 확인되는 스코프 경계

두 프로젝트 모두 "실제 주문 실행/브로커 연동/계좌 단위 하드 리스크 한도" 계층이 없고, 스스로도 "simulated exchange"(TradingAgents) / "backtesting"(Magents)라고 명시한다. 즉 organizational multi-agent 패턴(역할 분리, debate, pod 오케스트레이션)을 AIOS가 부분적으로 채택하더라도, 그 영향 범위는 **Strategy Factory Plane의 authoring/검증 로직에 한정**되며, AIOS의 기존 Policy Plane(계좌 레벨 하드 리밋, 컴플라이언스 게이트)이나 Execution Plane(실제 주문 라우팅)은 별도로 그대로 유지되어야 한다는 전제가 두 레포의 코드로 뒷받침된다. 두 프레임워크가 만들어내는 최종 산출물(rating/action/entry-stop-position 제안, pod별 시그널)은 모두 AIOS Strategy IR 쪽의 "제안값"으로만 편입되어야 하며, 어떤 경우에도 AIOS의 기존 리스크 하드 리밋을 우회하는 입력으로 취급해서는 안 된다.

신뢰도 측면에서는 TauricResearch/TradingAgents(논문 기반, 10만+ star, 활발한 유지보수)를 1차 참고 레퍼런스로, LLMQuant/Magents(소규모 개인 프로젝트, contributor 2명)를 "아이디어 스케치 수준의 2차 참고자료"로 구분해 인용하는 것이 타당하다.
