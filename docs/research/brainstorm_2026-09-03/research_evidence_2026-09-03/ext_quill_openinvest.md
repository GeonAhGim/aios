# 외부 레포 코드 레벨 분석 — quill-trading-agent (S: Risk Guardian/veto) & openInvest (S: agent 격리/cross-challenge/committee)

- 조사일: 2026-09-03
- 방법: `git clone --depth 1` 후 로컬에서 소스 직독. GitHub API로 health signal(커밋 수, contributor, star, 최종 push) 확인.
- 클론 경로(scratchpad, 산출물에는 미포함): `.../scratchpad/ext3/quill-trading-agent`, `.../scratchpad/ext3/openInvest`
- 컨텍스트: 이 페어는 이번 배치에서 최우선순위다. `AIOS_Codex_Research_Review_by_Fable_v1_2026-09-03.md`가 Quill을 "AI proposes, a separate trust-domain authority can veto"를 명시적 아키텍처 개념으로 삼는 유일한 Phase-2 후보로 지목했고, AIOS의 RiskEngine은 실무적으로 이미 이걸 하고 있지만 그 *프레이밍*이 외부 어딘가의 명시적 디자인 패턴과 일치하는지 검증된 적이 없었다. openInvest는 multi-agent governance/aggregation("auditable committee")에 대한 두 번째 독립 데이터 포인트다.

---

## 0. Health Signal 요약 (선-공개)

| 항목 | quill-trading-agent | openInvest |
|---|---|---|
| 생성일 | 2026-06-19 | 2025-12-19 |
| 마지막 push | 2026-06-24 (조사일 기준 약 2.5개월 정체) | 2026-09-03 (조사 당일에도 push, 활발) |
| 총 커밋 | 5 | 539(주 저자) + 160(github-actions bot) + 6 + 3 = 700+ |
| Contributor | 1명 (yogeshg665) | 사실상 1명(longsizhuo, 커밋 539개) + bot + 2명 소수 기여 |
| Star / Fork | 0 / 0 | 82 / 13 |
| 테스트 | 8개 파일, `pytest -q`로 전부 도는 구조. `test_risk.py` 175줄, risk check 단위 테스트 존재 | 94개 테스트 파일, GitHub Actions CI 실주행(`uv run pytest tests/ -v`), committee 전용 테스트 6개(`test_committee_*.py`) |
| 문서화 | README, AGENTS.md, skills/*.md, agents/*.md — Claude Code skill pack 형식 | README, 26개 ADR(`docs/wiki/adr/001~026`), wiki, CHANGELOG(release-please 자동화) |
| 실사용 흔적 | 없음(0 star, solo, paper 모드 전용, synthetic 데이터) | 82 star/13 fork, ADR에 실거래 이후 소급 수정 이력("2026-05-20 NDQ 실거래 33.6%→70.2% hallucination 발견 후 패치") — 실사용 중인 시스템으로 보임 |

**정직하게 말하면**: Quill은 코드 품질과 아키텍처 문서화는 훌륭하지만, 1인 저자·5커밋·0 star·2.5개월째 정체된 **작지만 잘 설계된 데모/PoC**다. 실거래 경험이나 외부 검증이 전무하다. openInvest는 규모·활동성·엔지니어링 성숙도(ADR 규율, 실패 사례를 기록하고 되짚는 습관, CI)에서 명백히 더 "실제로 돌아가는 시스템"이다. 다만 openInvest도 사실상 1인 프로젝트이고, 이하에서 보듯 "committee"의 실체는 격리된 프로세스가 아니라 **같은 프로세스 안의 스레드로 병렬 실행되는 별도 system prompt LLM 인스턴스들**이다 — 코드 자체가 이를 숨기지 않고 정확히 그렇게 문서화한다.

---

## Part A — quill-trading-agent

### A-0. 구조 개요

레포는 두 겹으로 되어 있다: `skills/`, `agents/*.md`는 Claude Code용 "Agent Skill" 명세(마크다운, LLM 오케스트레이터가 읽는 프롬프트/워크플로 문서)이고, `src/robinhood_agent/`는 그 명세를 그대로 구현한 **결정론적 Python 엔진**이다. `AGENTS.md`가 이 이중구조를 명시한다:

```
AGENTS.md:8-11
A pack of Agent Skills that propose and govern equities trades, plus a deterministic
Python engine that implements the same logic and serves as the executable reference
for every skill. A swarm of strategies proposes orders; an independent risk guardian
has final say.
```

파이프라인 순서(`src/robinhood_agent/agents/orchestrator.py:56-68`): intake → enrichment → macro(advisory) → **strategy swarm(제안)** → **risk guardian(평가)** → **decision(정책 적용)** → **execution(주문 집행)** → reporting.

### A-1. Risk Guardian은 별도 모듈/클래스인가, 무엇을 veto하는가, deterministic인가 LLM 판단인가

**별도 클래스, 별도 실행 스테이지.** `RiskGuardianAgent`(`src/robinhood_agent/agents/risk_guardian.py:11-41`)는 strategy swarm과 완전히 분리된 오케스트레이터 스테이지로, 전략이 만든 `proposals`를 입력으로만 받고 자신의 판단(리스크 체크 목록)은 전략 코드가 전혀 접근할 수 없는 별도 레지스트리에서 로드한다.

```python
# src/robinhood_agent/agents/risk_guardian.py:11-17
class RiskGuardianAgent(Agent):
    """Evaluates each order proposal against every risk check.

    The guardian is the authority that can veto an order. Each check runs in
    isolation so a single faulty check cannot suppress the others. Findings are
    returned keyed by proposal id for the decision agent.
    """
```

veto 대상은 **10개의 구체적·결정론적 규칙**(`src/robinhood_agent/risk/registry.py:18-29`)이다: `MarketHoursCheck`, `DailyLossCheck`, `OrderRateCheck`, `PositionSizeCheck`, `ConcentrationLimitCheck`, `BuyingPowerCheck`, `PdtCheck`(FINRA pattern-day-trader), `PriceDeviationCheck`, `WashSaleCheck`(IRS 30일), `LiquidityCheck`. 각 체크는 `config/config.yaml`의 숫자 임계값(예: `max_sector_pct: 0.40`, `max_position_pct: 0.20`, `max_daily_loss_pct: 5.0`)을 그대로 비교하는 순수 산술이다. 예:

```python
# src/robinhood_agent/risk/concentration_limit.py:32-44
projected = (current_sector_value + proposal.notional) / total
if projected > settings.max_sector_pct:
    return self._finding(
        name="sector_concentration_exceeded",
        severity=85.0,
        blocking=True,
        rationale=(
            f"Buying {proposal.symbol} would raise the '{sector}' sector "
            f"weight to {projected:.1%}, above the "
            f"{settings.max_sector_pct:.1%} limit."
        ),
        evidence={"sector": sector, "projected_weight": round(projected, 4)},
    )
```

**LLM은 이 판단 경로에 전혀 개입하지 않는다.** 코드베이스 전체에서 `LLMClient`를 import하는 곳은 `orchestrator.py`, `reporting_agent.py`, `pipeline/trading_pipeline.py` 세 곳뿐이며, 실제로 LLM을 호출하는 건 `ReportingAgent`(내러티브 생성용) 하나뿐이다. `RiskCheck.evaluate()`는 `abstractmethod`로 강제되는 순수 함수이고, `risk/base.py`의 docstring이 이를 명문화한다:

```python
# src/robinhood_agent/risk/base.py:1-7
Every check inspects a single :class:`OrderProposal` against the portfolio, the
market snapshot, and the configured limits, and returns at most one
:class:`RiskFinding`. A check is deterministic and must never place orders. A
``blocking`` finding is a hard limit breach that vetoes the order.
```

즉 "AI가 veto를 판단한다"는 브랜딩과 달리, veto 자체는 완전한 규칙 엔진이고 "Agent"라는 이름은 오케스트레이션 상의 역할 구분일 뿐이다. 이는 오히려 이 레포가 참고 사례로서 더 명확한 근거가 된다 — veto가 LLM judgment였다면 "별도 trust-domain authority"라는 주장 자체가 성립하지 않았을 것이다.

**전략 생성 AI는 guardian 규칙을 보거나 바꿀 수 없다.** `StrategySwarmAgent`(`src/robinhood_agent/agents/strategy_swarm.py:24-41`)는 `context.proposals`만 채워 넣고 종료하며, `risk/` 패키지를 import하지 않는다. 개별 전략(`strategies/momentum.py` 등)도 `config.risk`를 참조하지 않는다 — 참조하는 값은 `config.strategy.*`뿐이다. 두 스테이지가 같은 `config.yaml` 파일을 공유하긴 하지만(파일 시스템 레벨 공유), 코드 경로상 전략 로직은 리스크 임계값을 읽지도 조작하지도 못한다. `references/risk-limits.md`와 `agents/risk-officer.md`가 이 경계를 조직 규범으로도 명문화한다:

```
agents/risk-officer.md:16-18
- A blocking finding vetoes the order. Conviction never overrides a limit.
- Every finding carries a severity, a blocking flag, and a plain-language rationale.
```

### A-2. Veto는 주문 실행 전인가 후인가 — 실질적 enforcement point가 있는가

**실행 전, 그리고 실제 enforcement point가 존재한다.** 파이프라인은 `guardian.run() → decision.run() → execution.run()` 순서로 강제되어 있고(`orchestrator.py:62-64`), `DecisionAgent`가 findings를 결정론적으로 3가지 outcome(`ALLOW` / `REQUIRE_APPROVAL` / `BLOCK`)으로 매핑한다:

```python
# src/robinhood_agent/agents/decision_agent.py:36-41
if has_blocking and policy.block_on_critical:
    outcome = DecisionOutcome.BLOCK
elif policy.require_manual_approval or risk_score >= policy.approval_threshold:
    outcome = DecisionOutcome.REQUIRE_APPROVAL
else:
    outcome = DecisionOutcome.ALLOW
```

그리고 `ExecutionAgent`는 **`ALLOW`가 아닌 결정은 브로커에 전달조차 하지 않는다**:

```python
# src/robinhood_agent/agents/execution_agent.py:1-6, 34-35
"""Execution agent: route approved orders to the broker adapter.

Only ``ALLOW`` decisions are sent to the broker. Orders held for human approval or
blocked by the guardian are recorded but never placed. ...
"""
            if decision.outcome is DecisionOutcome.ALLOW:
                try:
                    reports.append(broker.place_order(proposal))
```

`BLOCK`/`REQUIRE_APPROVAL`인 proposal은 `place_order()` 자체가 호출되지 않고 상태만 `"blocked"`/`"held"`로 기록된다. 이는 로깅에 그치는 advisory가 아니라, **주문 배치 함수 호출 자체를 코드 분기로 막는** 실질적 게이트다. `config.yaml`도 이를 명문화: `block_on_critical: true # a blocking finding always vetoes the order`.

### A-3. AIOS 시사점 (Part A)

AIOS의 **Policy Plane (Deterministic PDP)**은 이미 "금융 행위 정책의 단일 결정 지점, 결과는 결정론적"(I-01, I-09)이라는 원칙을 갖고 있고, `mandate ∩ RiskEngine 최소값`을 계산하는 합성 지점으로 설계되어 있다. Quill의 `DecisionAgent`는 이 개념을 사실상 축소판으로 그대로 구현하고 있다 — `risk_score = max(finding.severity)` + `has_blocking` 두 신호를 하나의 결정론적 정책(`block_on_critical`, `approval_threshold`)에 합성해 `ALLOW/REQUIRE_APPROVAL/BLOCK` 세 값 중 하나로 떨어뜨리고, 그 결과를 실행 계층의 분기 조건으로 강제한다는 점에서 AIOS Policy Plane의 "단일 결정 지점 + 결정론적 결과" 요건과 구조적으로 동형이다.

**새로 배우는 것은 크지 않다.** Quill이 보여주는 패턴 — (1) 별도 클래스/스테이지로 분리된 veto 권한, (2) 결정론적 규칙만으로 구성, (3) 제안자가 규칙에 접근 불가, (4) 실행 전 하드 게이트 — 은 AIOS의 RiskEngine + Policy Plane 조합이 이미 갖추고 있는 것과 정확히 같은 골격이다. 다만 두 가지는 확인할 가치가 있었다:

1. **"AI proposes, separate trust-domain authority vetoes"라는 프레이밍이 실제로 외부에 존재하는 명시적 디자인 언어라는 확인.** Quill의 README/AGENTS.md/skill 문서는 이 문구를 거의 그대로 쓴다("The risk guardian has final say... cannot be overridden by a strategy's conviction"). 이는 AIOS RiskEngine의 설계가 재발명이 아니라 업계(적어도 OSS 트레이딩 에이전트 커뮤니티)에서 수렴적으로 나타나는 패턴임을 뒷받침하는 독립 근거다.
2. **경고 신호**: Quill에서도 "veto가 결정론적이려면 veto 자체와 그 입력(제안자)이 물리적으로 분리된 코드 경로여야 한다"는 원칙이 지켜지는데, 그 근거는 강한 process/sandbox 격리가 아니라 **단순히 별도 Python 모듈 + import 방향 규율**이다. AIOS가 이미 이보다 강한 보장(별도 서비스/PDP 프로세스)을 갖고 있다면 이는 AIOS가 앞서 있다는 뜻이고, 반대로 AIOS의 격리도 "import 안 하면 됨" 수준이라면 Quill과 동급의 취약점(개발자가 실수로 strategy 코드에서 risk config를 참조하게 만들 위험)을 안고 있다는 뜻이 된다 — 이 지점은 코드 리뷰 룰(예: lint로 strategies/ 하위에서 risk/ import 금지)로 방어했는지 AIOS 쪽에서 별도로 확인할 가치가 있다.

결론: Quill은 AIOS가 이미 잘하고 있는 것을 **확인해주는 사례**이지, 새로운 아키텍처 아이디어를 주는 사례는 아니다. 유일한 실질적 시사점은 "veto 규칙에 대한 제안자의 접근 차단"을 정적 검사(import lint, 모듈 경계 테스트)로 강제하고 있는지 AIOS 쪽에서 점검하라는 것이다.

---

## Part B — openInvest

### B-0. 구조 개요, 활동성

openInvest는 82 star, 13 fork, 539커밋(주 저자), 조사 당일에도 push가 있었던 **활발히 운영 중인 개인 프로젝트**다. `docs/wiki/adr/`에 26개의 ADR이 있고, 코드 주석이 실거래 중 발견한 구체적 버그(예: "2026-05-20 NDQ 真实 33.6% 编成 70.2%" — 실거래 포지션의 집중도를 LLM이 70.2%로 hallucinate한 사건)를 근거로 방어 코드를 정당화하는 패턴이 반복된다. 즉 이 시스템은 최소한 저자 본인의 실계좌에서 실제로 운용되고 있는 것으로 보이며, "toy"가 아니라 **가동 중인 시스템이 실패를 겪고 코드로 학습해온 흔적**이 뚜렷하다.

committee 로직은 두 레이어로 나뉜다: `src/openinvest/capabilities/committee/{cio,quant,risk_officer,macro_strategist}/*.md`(각 역할의 system prompt 정의)와 `src/openinvest/core/committee/*.py`(오케스트레이션 실행 코드).

### B-1. Multi-agent 격리 방식, "cross-challenge"의 코드상 실체, committee 집계 방식

**격리 = 프로세스/샌드박스가 아니라 "별도 system prompt + 정보 분리"다.** 4개 역할(Macro Strategist, Quant, Risk Officer, CIO)은 각각 독립된 `SDKAgent` 인스턴스(자체 system prompt, 자체 대화 컨텍스트)로 생성되고, 동일 프로세스 내 `ThreadPoolExecutor`로 병렬 호출된다. 코드가 이를 정확히 그렇게 문서화한다:

```python
# src/openinvest/core/committee/agent_io.py:136-141
def _parallel_ask(pairs: List[Tuple[Optional[SDKAgent], str]]) -> List[str]:
    """并行跑多个 (agent, input)，返回结果列表（按入参顺序）

    DeepSeek API 是 IO 密集型（HTTP），ThreadPool 不受 GIL 影响。
    Round 1 / Round 2..N 内部的 Quant + Risk 就用这个并行起来，省 50% 耗时。
    """
```

격리의 실질적 의미는 (a) **입력 정보 분리** — Quant는 시장 데이터만, Risk Officer는 포트폴리오/과거 행동 패턴만 받는 별도 프롬프트(`quant_input_r1` vs `risk_input_r1`, `debate.py:178-191`)이고, (b) **장애 격리** — 한 worker가 실패해도 다른 worker 호출을 막지 않으며, 실패 시 자연어 에러 대신 `[WORKER_UNAVAILABLE]`이라는 명시적 sentinel을 반환해 CIO가 "이 위에서 종합했다간 오염된 판단"임을 식별하게 한다(`agent_io.py:99-109`, 아래 인용). OS 프로세스 격리나 컨테이너 샌드박스는 전혀 없다 — 이는 코드가 스스로 밝히는 사실이며 과장할 필요가 없다.

```python
# src/openinvest/core/committee/agent_io.py:103-112
def _ask(agent: Optional[SDKAgent], context: str) -> str:
    """LLM 调用 + 重试。失败时返回明确的哨兵字符串，让 CIO prompt 可识别降权。

    audit (algo M4): 之前失败返回 'Agent error: ...' 这种自然语言，CIO 会
    礼貌地尝试综合错误消息，输出 silent corruption 的 verdict。现在返回
    带 [WORKER_UNAVAILABLE] 前缀，CIO prompt 已加 hard rule 看到此标记必须
    把 confidence 压到 ≤ 0.4 + verdict 必须 HOLD。
    """
```

**"cross-challenge"의 구체적 코드 실체**: `run_committee()`(`debate.py:100-368`)는 Round 1(독립 진술) → Round 2..N(교차반박) → CIO 종합의 3단 구조다. Round 1에서 Quant와 Risk Officer는 서로의 출력을 보지 못한 채 병렬로 각자 의견을 낸다. Round 2부터는 `_format_debate_history(quant_history, risk_history)`로 만든 전체 토론 이력을 **서로에게 보여주고** 재판단을 요구한다:

```python
# src/openinvest/core/committee/debate.py:227-235
quant_input_rN = (
    regime_section
    + f"# 现在是第 {round_idx} 轮 cross-challenge（最多 {max_debate_rounds} 轮）\n\n"
    + debate_block
    + "\n\n# 任务\n"
    + "请基于完整辩论历史调整或维持你的 SIGNAL/STRENGTH。"
    ...
```

수렴 판정(`_check_convergence`, `debate_calc.py`로 분리됨)이 있어 의견이 2라운드 연속 안정되면 조기 종료한다. `max_debate_rounds=1`(daily_report 배치용)과 `max_debate_rounds=4`(live 엔드포인트용) 두 모드가 있다.

**집계 방식 = 투표가 아니라 단일 arbiter agent(CIO).** Quant와 Risk Officer 사이에 수치적 투표나 가중합은 없다. 대신 두 역할의 전체 라운드 이력(`to_cio_brief()` + 전체 `_format_debate_history`)을 하나의 CIO LLM에게 통째로 넘기고, CIO가 자연어 memo(`VERDICT/CONFIDENCE/DOMINANT_VIEW/...`)를 산출한다. 즉 "committee"의 최종 결정 메커니즘은 **다수결이 아니라 4번째 역할의 자유재량적 종합**이다.

### B-2. "Auditable"의 구체적 실체 — 재구성 가능한 append-only 기록인가

**부분적으로 그렇다, 그러나 구조화 수준은 markdown + JSONL 정도다.** 결정 하나마다 다음 세 계층이 남는다:

1. **`_persist()`가 쓰는 committee markdown** (`persist.py:60-137`, 경로 `memory/.committee/<date>/<symbol>.md`): CIO memo 원문, Macro/Quant/Risk 각 라운드 원문, 라운드 수·수렴 여부, 그리고 사후 귀인용 매크로 스냅샷(VIX/TNX/환율)을 **결정 시점 값으로 고정**해서 함께 저장한다.

```python
# src/openinvest/core/committee/persist.py:30-35
def _capture_macro_context(as_of_date: Optional[str] = None) -> Dict[str, Any]:
    """快照决议时的 macro 状态（给 verdict_review 做事后归因用）。

    audit A1: verdict 错时能区分'模型预判错' vs '宏观突变黑天鹅'。
    例：BUY 后 60 天跌 8%，但同期 VIX +60% → 不是模型差，是黑天鹅冲击。
    """
```

2. **append-only JSONL 이벤트 로그** (`.dreams/events.jsonl`, `memory_store.py:275-280`): 매 committee 실행마다 `{ts, phase:"committee_finished", asset, verdict, confidence, macro_at_decision, debate_rounds, debate_converged}`를 한 줄씩 append. `_file_lock`으로 동시성 보호.

3. **`trades_db.py`의 `trades` 테이블**: 실제 체결된 거래를 `verdict_id`(형식 `"<date>/<symbol>"`) 컬럼으로 위 committee markdown 파일에 역참조 가능하게 연결한다.

이 세 계층을 조합하면 "왜 이 거래가 나갔는가"를 사후에 재구성할 수 있다: `trades.verdict_id` → `.committee/<date>/<symbol>.md`(누가 무엇을 제안했고 CIO가 왜 그렇게 종합했는지 원문) → `.dreams/events.jsonl`(그 결정이 언제, 어떤 신뢰도로 내려졌는지의 append-only 타임라인). 다만 이것은 **markdown 자유 텍스트 + 로그 파일**이지, 질의 가능한 정규 스키마(SQL 테이블에 role/claim/vote/이유를 컬럼화)는 아니다 — 참고로 별도 하위시스템인 `db/event_store.py`(뉴스/매크로 "사건" 저장용 SQLite+벡터 인덱스)는 훨씬 정규화되어 있지만, 이건 committee 판단 자체의 감사 기록이 아니라 committee의 *입력 자료*(뉴스 이벤트) 저장소다.

**더 흥미로운 지점은 "auditable"이 사후 기록에 그치지 않고, LLM 출력에 대한 코드 레벨 검증·override 계층으로도 존재한다는 것**이다. `parse_cio_memo()`(`cio_parse.py:138-352`)는 CIO의 자연어/JSON 출력을 파싱한 뒤 **6개의 결정론적 sanity check**를 강제 적용한다 — 예를 들어 BUY + confidence≥0.95는 자동으로 ACCUMULATE + confidence 하향(과신 방지), `[WORKER_UNAVAILABLE]` 마커가 브리핑에 있으면 verdict를 무조건 HOLD로 강제:

```python
# src/openinvest/core/committee/cio_parse.py:228-239
# Sanity check 3（audit algo M4）: worker 输入失败时 confidence 降级
floor = _verdict_cfg.worker_unavailable_confidence_floor
_wu = "[WORKER_UNAVAILABLE]" in text or (
    worker_brief is not None and "[WORKER_UNAVAILABLE]" in worker_brief
)
if _wu and out["confidence"] > floor:
    out["_original_confidence_unavailable"] = out["confidence"]
    _force_hold(out, confidence_ceiling=floor)
```

그리고 리스크 관련 수치(포트폴리오 집중도)는 LLM이 hallucinate하는 것을 막기 위해 **portfolio_summary의 문자 그대로의 참값으로 강제 덮어쓴다**:

```python
# src/openinvest/core/committee/cio_parse.py:387-402 (요약 발췌)
def _override_concentration_in_risk_output(risk_output, true_pct):
    """把 Risk Officer 输出里的 CONCENTRATION_PCT 强制覆写为 portfolio_summary 字面值
    背景（2026-05-20 漂移修复）：... Risk Officer LLM ... 仍偶发
    hallucinate 编成 70.2%（同 prompt 前一日还能输出 33.4%）。"""
```

이는 매우 중요한 구분점이다: **openInvest의 Risk Officer는 Quill의 Risk Guardian과 성격이 다르다.** Risk Officer는 committee의 대등한 토론 참가자(LLM 하나)일 뿐, 다른 에이전트의 제안을 거부할 권한을 가진 별도 trust-domain 기관이 아니다. 실제로 최종 veto/override 권한을 쥔 것은 Risk Officer 에이전트가 아니라 **`parse_cio_memo`라는 순수 코드 계층**이다 — CIO(LLM)의 출력을 신뢰하지 않고 사후에 결정론적 규칙으로 강제 정정한다는 점에서, 이는 오히려 "LLM 판단 위에 얹힌 deterministic PDP" 패턴에 더 가깝다.

### B-3. AIOS 시사점 (Part B)

openInvest가 실제로 새로 알려주는 것과, AIOS가 이미 앞서 있는 것을 나눠서 본다.

**AIOS가 이미 앞서 있는 부분**: openInvest의 "committee"는 이름과 달리 투표 기반 다자간 거버넌스가 아니라 "3명의 조언자 + 1명의 최종 재량 결정자(CIO)"에 가깝다. 최종 결정이 여전히 LLM(CIO)의 자유재량이라는 점, 그리고 그 LLM 출력을 코드가 사후에 clamp/override하는 방식(`parse_cio_memo`)은, **결정 자체가 사전에(pre-hoc) 결정론적 정책으로 계산되는 AIOS Policy Plane 방식보다 약하다.** AIOS의 `mandate ∩ RiskEngine 최소값`은 LLM 출력을 기다렸다가 사후 교정하는 게 아니라애초에 정책 교차 계산이 유일한 결정 경로다. openInvest 쪽 아키텍처는 "LLM이 먼저 결정하고, 코드가 사후에 이상한 값만 잘라낸다"는 점에서 I-01/I-09가 요구하는 "결과는 결정론적"이라는 기준에 완전히는 부합하지 않는다 — verdict 자체(BUY/HOLD/SELL 방향)는 여전히 비결정론적 LLM 출력이고, 코드는 그 출력의 극단값만 clamp한다.

**openInvest가 실제로 주는 새 데이터 포인트 두 가지**:

1. **"cross-challenge"는 실제로 존재하는, 재현 가능한 패턴이다** — 단순 병렬 실행이 아니라 라운드별로 상대방의 이전 출력 전체를 다음 프롬프트에 주입하고 명시적으로 "조정하거나 유지하라"고 요구하는 구조, 그리고 수렴 판정으로 라운드를 조기 종료하는 로직까지 갖추고 있다. AIOS가 향후 "여러 전략/판단 소스 간의 명시적 반박 라운드"를 설계한다면, 이 레포는 (a) 라운드별 정보 분리 → 공개 → 재요청의 3단 패턴, (b) `[WORKER_UNAVAILABLE]` 같은 실패 sentinel을 통한 "이 위에서 종합하면 안 된다"는 명시적 신호 전파, (c) 수렴 조기종료 설계의 참고가 된다.
2. **"Risk 역할이 committee 멤버로 들어가는 것"과 "Risk가 별도 trust-domain veto 권한을 갖는 것"은 다른 아키텍처라는 대조 사례가 확보됐다.** openInvest는 전자이고 AIOS/Quill은(RiskEngine이 committee 밖의 게이트인 한) 후자다. 이 대조는 AIOS 설계 문서에 "우리 RiskEngine은 committee의 한 목소리가 아니라 committee 밖의 game이다"라는 차별점을 명시적으로 적을 근거가 된다 — 실제로 openInvest 자체도 이 문제를 겪었다: Risk Officer의 판단(집중도 수치)을 LLM이 hallucinate하자, 해법은 "Risk 역할의 발언권을 더 세게 만드는 것"이 아니라 **Risk 관련 사실을 아예 코드가 committee 밖에서 강제로 주입/덮어쓰는 것**이었다(`_override_concentration_in_risk_output`). 이는 "risk-critical한 사실은 LLM 토론에 맡기지 말고 결정론적으로 고정하라"는 원칙이 openInvest 저자 스스로의 실패-수정 경험에서 도출됐다는 뜻이며, AIOS가 이미 채택한 노선(RiskEngine을 별도 결정론적 권한으로 분리)이 옳았다는 **독립적인 사후 확인**이 된다.

**"auditable"에 대한 감사**는 AIOS Policy Plane이 요구하는 Event Ledger 수준의 엄밀함(I-09: "조회가 실제로 일어났다는 것을 Event Ledger에 남긴다")에는 못 미친다 — openInvest는 markdown 자유 텍스트 + JSONL 로그 조합으로, 사람이 읽고 사후 재구성하기엔 충분하지만 질의 가능한 구조화 스키마는 아니다. 이 점에서도 AIOS가 이미 더 엄격한 기준을 갖고 있다고 판단된다.

### B-4. 결론 (Part B)

openInvest는 "auditable investment-committee-style aggregation"이라는 태그가 붙은 근거를 실제로 갖고 있지만(4-role, 라운드제 cross-challenge, markdown+JSONL 감사 기록), (1) 최종 결정 메커니즘은 투표/합의가 아니라 단일 LLM(CIO)의 재량이고, (2) "risk"는 별도 trust-domain 권위가 아니라 committee 내부의 대등한 발언자이며 실질적 veto/override는 committee 밖의 순수 코드(`parse_cio_memo`)가 담당하고, (3) 감사 기록은 구조화 DB가 아니라 사람이 읽는 문서+로그 수준이다. 세 가지 모두에서 AIOS의 기존 설계(Policy Plane 사전 결정론, RiskEngine의 committee-외부 권한, Event Ledger 지향)가 이미 더 엄격한 기준을 채택하고 있다. openInvest는 AIOS에 새 아키텍처를 가르쳐주기보다는, **"멀티에이전트 토론에 risk 판단을 맡기면 hallucination이 실거래 사고로 이어진다"는 실패 사례를 통해 AIOS가 이미 내린 결정(risk는 committee 밖에 둔다)이 옳았음을 재확인**시켜주는 자료로 보는 것이 정확하다.

---

## 종합 결론

두 레포 모두 "AI proposes, deterministic/separate authority disposes"라는 동일한 상위 패턴을 서로 다른 성숙도와 서로 다른 방식으로 구현하고 있었다. Quill은 그 패턴을 교과서적으로 순수하게 구현한 소규모 PoC(veto=100% 결정론, 실행 전 하드 게이트, 제안자-veto 코드 분리)였고, openInvest는 실거래 경험을 통해 "committee 내부에 risk를 넣는 것만으로는 부족하고, risk-critical한 사실은 committee 밖에서 결정론적으로 고정해야 한다"는 결론에 실패를 겪으며 도달한, 더 크고 활발하지만 덜 순수한 사례였다. 두 경우 모두 AIOS의 기존 Policy Plane/RiskEngine 설계를 반박하는 근거는 없었고, 오히려 **독립적인 두 경로에서 같은 결론에 수렴했다는 확증(convergent validation)**을 제공한다. 이번 조사에서 AIOS의 설계를 바꿔야 할 근거는 발견되지 않았다; 유일한 실행 가능한 후속 조치는 (a) AIOS의 strategy/policy 코드 경계가 import-lint 등 정적 검사로 실제로 강제되는지 점검하는 것(Quill이 보여준 잠재적 취약점), (b) 향후 다중 판단 소스 간 명시적 반박(cross-challenge) 라운드가 필요해질 경우 openInvest의 라운드/수렴/실패-sentinel 패턴을 참조 구현으로 삼는 것이다.
