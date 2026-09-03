# 외부 레포 코드 레벨 분석 — coinjure / mantle-quant / agenttrader

조사일: 2026-09-03. 세 레포 모두 `git clone --depth 1`로 shallow clone 후 코드를 직접 읽고, 건강 신호(health signal)는 GitHub REST API(`gh api`)로 별도 조회했다(shallow clone은 커밋 1개만 보여 `git log`로 기여자 수를 셀 수 없어 API를 사용). 세 레포 모두 clone은 성공했다.

## 공통 건강 신호 요약

| 레포 | 생성일 | 마지막 push | stars/forks/issues | 기여자 수 | 커밋 수 | 테스트 |
|---|---|---|---|---|---|---|
| ulab-uiuc/coinjure | 2025-02-10 | 2026-05-05 | 35 / 4 / 5 | 7 | 100+ | 32개 test 파일, pytest 기반 |
| ryonzhang/mantle-quant | 2026-06-11 | 2026-06-14 (3일 후) | 1 / 0 / 0 | 1 | 7 | vitest 44개 (통과), hardhat 계약 테스트 별도 |
| finnfujimura/agenttrader | 2026-02-24 | 2026-03-07 (11일 후) | 12 / 0 / 0 | 1 | 86 | 32개 test 파일 (unit+integration) |

세 프로젝트의 성숙도는 뚜렷이 다르다. coinjure는 University of Illinois (ulab-uiuc) 소속 팀이 만든, 7명이 기여하고 지금(2026-09-03)까지도 활성 상태로 보이는(마지막 push 5월) 실제 배포형 프로젝트다. agenttrader는 1인 저자이지만 86 커밋·32개 테스트 파일로 짧은 기간(2주) 안에 상당히 완성도 있게 만들어졌고, 이후 6개월간 업데이트가 없다(사실상 활동 정지). mantle-quant는 "Turing Test Hackathon 2026" 제출물로, 생성부터 마지막 push까지 3일, 커밋 7개, 별 1개, 기여자 1명 — 전형적인 해커톤 1회성 산출물이며, 아래에서 보듯 실제로 핵심 경로가 깨져 있다.

---

## Part A — coinjure (discovery → backtest → live 파이프라인)

### 저장소 개요

coinjure는 이름과 달리 암호화폐가 아니라 **예측시장(prediction market)** — Polymarket/Kalshi — 트레이딩 에이전트 하네스다. README가 명시하는 파이프라인:

```
LLM Agent + market data
    → [1. Discovery]  — discover cross-market relations via CLI
    → [2. Backtest]   — validate each relation against historical data
    → [3. Execution]  — paper-trade or live-trade validated strategies
    → [4. Monitoring]  — human operator monitors and intervenes when needed
```

코드 구조도 이 4단계에 대응하는 실제 별도 모듈로 나뉘어 있다: discovery는 `coinjure/market/auto_discover.py` + `coinjure/market/relations.py`(`MarketRelation` 데이터클래스와 `RelationStore` 영속 저장소), backtest는 `coinjure/engine/backtester.py`, paper/live 실행은 `coinjure/engine/engine.py` + `coinjure/cli/engine_commands.py`, 모니터링은 `coinjure/cli/textual_monitor.py`(TUI)다. 즉 **1번 질문("한 스크립트가 다 하는지")에 대한 답은 "아니다" — 4단계가 실제로 분리된 코드 모듈이다.**

### 승급(promotion) 게이트의 실체

문제는 "게이트가 있는가"다. relation의 lifecycle은 `pending_backtest → backtest_passed/backtest_failed → paper_trading → deployed(live) → retired`로 정의되어 있고(`coinjure/engine/registry.py`), CLI에 `engine promote` 커맨드도 별도로 존재한다:

```python
@engine.command('promote')
@click.option(
    '--all', 'promote_all', is_flag=True, default=False,
    help='Promote all paper_trading entries with positive PnL.',
)
...
    if promote_all:
        entries = [
            e for e in reg.list() if e.lifecycle == 'paper_trading' and e.relation_id
        ]
        promoted = []
        for entry in entries:
            ...
            rel.status = 'deployed'
            store.update(rel)
            entry.lifecycle = 'deployed'
            reg.update(entry)
            promoted.append(entry.relation_id)
```
(`coinjure/cli/engine_commands.py:1730-1793`)

help 텍스트는 "positive PnL" 항목만 승급한다고 말하지만, 실제 필터 조건은 `e.lifecycle == 'paper_trading'`뿐이다 — **PnL 재확인 코드가 전혀 없다.** 즉 paper 단계에 들어간 relation은 실제 손익과 무관하게 `--all`로 한 번에 deployed(live 자격)로 승급된다. help 문서와 구현이 불일치하는 사례다.

한 단계 앞의 backtest→paper 게이트는 실제로 존재한다. `engine paper-run --all-relations`는 `relation_status='backtest_passed'`인 relation만 배치 실행 대상으로 필터링한다(`_run_batch(relation_status='backtest_passed', ...)`, `engine_commands.py:626`). 그리고 `backtest_passed` 여부를 결정하는 로직은 다음이 전부다:

```python
return BacktestResult(
    **result_base,
    total_pnl=pnl,
    trade_count=trade_count,
    passed=pnl > 0,
)
```
(`coinjure/engine/backtester.py:275-280`)

즉 게이트 기준은 **단일 backtest 런의 PnL > 0** 하나뿐이다. 최소 거래 횟수, 통계적 유의성 검정, walk-forward/out-of-sample 분리, Sharpe·MDD 등 리스크 조정 지표 — 이런 것은 전혀 없다. `passed=pnl > 0`은 동전 던지기로도 50%는 통과하는 기준이라, "게이트가 있다"고 부르기엔 매우 약하다.

더 결정적인 것은 **이 게이트가 `--all-relations` 배치 경로에서만 적용된다는 점**이다. `engine paper-run --strategy-ref X`처럼 단일 전략을 직접 지정하면 backtest 이력과 무관하게 바로 paper 세션이 시작되고, `engine live-run --strategy-ref X` 역시 `--all-relations`를 쓰지 않으면 `deployed` 상태 체크 없이 바로 실행된다:

```python
if not strategy_ref:
    raise click.ClickException(
        '--strategy-ref is required unless using --all-relations.'
    )
if detach:
    cmd = shlex.split(_coinjure_cmd()) + [
        'engine', 'live-run', '--no-detach',
        '--strategy-ref', strategy_ref, '--exchange', exchange,
    ]
    ...
    proc = subprocess.Popen(cmd, ...)
```
(`coinjure/cli/engine_commands.py:882-907`, `_confirm_live_trading`는 865번 줄에서 이보다 먼저 호출됨)

`live-run`에 있는 유일한 안전장치는 `_confirm_live_trading()`이며, 그 실체는 단순 y/n 프롬프트다:

```python
def _confirm_live_trading(*, as_json: bool) -> None:
    """Require explicit user confirmation before starting live trading."""
    ...
    if as_json:
        raise click.ClickException(
            'Live trading confirmation required in interactive mode.'
        )
    click.echo(click.style(disclaimer, fg='yellow'))
    confirmed = click.confirm(
        'Proceed with live trading?', default=True, show_default=True,
    )
    if not confirmed:
        raise click.ClickException('Live trading cancelled by user.')
```
(`coinjure/cli/engine_commands.py:48-65`)

`default=True`인 interactive confirm이라 엔터만 누르면 통과한다. `--json`(에이전트 자동화) 모드에서는 아예 예외를 던져 인터랙티브 터미널이 아니면 진행이 막히므로, 이 부분만은 "LLM 에이전트가 사람 확인 없이 완전 자동으로 실거래까지 직행하는 것"을 CLI 레벨에서 억제하는 효과가 있다 — 다만 이것은 통계적 성과 게이트가 아니라 인터랙션 방식의 우연한 부작용에 가깝다.

### 그 밖의 안전장치 (P0 guard)

한편 `tests/test_p0_guards.py`가 보여주듯 coinjure에는 운영 안전장치는 실제로 갖춰져 있다: 중복 주문 idempotency(`test_duplicate_client_order_id_rejected`), read-only 모드로 신규 주문 차단(`test_read_only_blocks_new_orders`), kill-switch 파일 기반 긴급 정지(`test_kill_switch_file_blocks_orders`), pause/resume 토글(`test_control_pause_resume_toggles_read_only`) 등. 즉 **"운영 중 사고 차단"(kill switch, idempotency)은 잘 만들어져 있지만, "성과가 검증되지 않은 전략이 live에 진입하는 것을 막는 승급 게이트"는 약하거나(배치 경로) 아예 없다(단일 전략 경로).**

### AIOS 시사점

coinjure는 discovery→backtest→execution→monitoring이 별도 코드 모듈로 분리된 좋은 참고 사례이지만, "backtest 통과해야 live 간다"는 약속은 (1) `passed = pnl > 0`이라는 임계값 없는 단일 지표, (2) `promote --all`이 help 문서와 달리 PnL을 재검증하지 않는 점, (3) 단일 `--strategy-ref` 경로가 게이트 자체를 우회할 수 있는 점 때문에 사실상 무력화되어 있다. AIOS의 Strategy Registry가 승급 게이트를 설계할 때는 (a) 게이트 판정 로직이 모든 진입 경로에서 동일하게 강제되는지(우회 가능한 "빠른 길"이 없는지), (b) help/문서에 적힌 조건과 실제 필터 조건이 반드시 코드 레벨에서 일치하는지를 CI로 검증하는 것이 coinjure의 실패 사례에서 얻을 수 있는 직접적 교훈이다. kill-switch·idempotency·pause 같은 런타임 안전장치는 coinjure 쪽이 참고할 만한 성숙한 구현이라 볼 수 있다.

---

## Part B — mantle-quant (provenance / tamper resistance)

### 저장소 개요와 클레임

mantle-quant는 Mantle Sepolia 테스트넷에 신호를 커밋하는 "검증 가능한 AI 트레이딩 시그널" 해커톤 프로젝트다. README의 핵심 주장:

> Every signal is committed to Mantle blockchain *before* the outcome is known. ... **the only AI trading agent in this hackathon that proves its edge with out-of-sample statistics.**
> AI trading systems claim impressive results — but those numbers are privately computed and impossible to verify. Any system can cherry-pick a favorable backtest window after the fact.

이 주장은 두 갈래다: (1) on-chain signal commitment로 사후 cherry-picking 방지, (2) walk-forward backtest + Brier score로 통계적 edge 증명. 앞서 확인했듯 실제 저장소는 생성 3일 만에 멈춘 해커톤 제출물(1인, 7 커밋)이다.

### "provenance/tamper resistance"의 코드 실체 — 부분적으로 실재, 그러나 실제 실행 경로는 깨져 있음

`SignalRegistry.sol`은 실제로 진지하게 설계된 append-only on-chain 레코드다:

```solidity
struct Signal {
    uint256  id;
    address  agent;
    string   asset;
    Direction direction;
    uint16   confidence;
    uint128  entryPrice;
    uint32   horizon;
    uint48   timestamp;
    bytes32  analysisHash;   // keccak256 of off-chain JSON analysis
    uint128  exitPrice;
    bool     resolved;
    int64    returnBps;
}
...
function resolveSignal(uint256 id, uint128 exitPrice) external {
    Signal storage s = signals[id];
    require(!s.resolved, "MQ: already resolved");
    require(s.agent == msg.sender || msg.sender == owner, "MQ: not authorized");
    require(exitPrice > 0, "MQ: zero exit price");
    require(
        block.timestamp >= uint256(s.timestamp) + uint256(s.horizon) * 60,
        "MQ: horizon not elapsed"
    );
    ...
}
```
(`contracts/SignalRegistry.sol`)

즉 신호를 먼저(outcome을 모르는 시점에) `recordSignal`로 커밋하고, horizon 경과 후에만 `resolveSignal`로 1회성 정산이 가능하며, 이미 resolve된 신호는 재수정 불가능하다 — "결과를 안 뒤에 유리한 신호만 골라 발표"하는 사후 편집(post-hoc editing)은 구조적으로 막는다. 이 자체는 tamper-evidence로서 정직한 설계다.

그러나 정작 이 hash를 만들어 체인에 쓰는 오프체인 코드 경로가 깨져 있다. `src/agent/onchain.ts`는 다음과 같이 `analyzer.js`에서 `hashAnalysis`를 import해서 쓴다:

```typescript
import { hashAnalysis }      from "./analyzer.js";
...
async function writeSignal(analysis: AnalysisResult): Promise<OnChainSignal> {
    ...
    const analysisHash  = hashAnalysis(analysis);
    ...
    const tx = await registry.recordSignal(
      analysis.asset, directionEnum, analysis.confidence,
      entryScaled, analysis.horizon, analysisHash,
    );
```
(`src/agent/onchain.ts:9, 86-97`)

그러나 `src/agent/analyzer.ts`는 `hashAnalysis`라는 이름의 함수를 **export하지 않는다.** analyzer.ts는 hash를 함수로 분리하지 않고 `analyzeAsset()` 내부에서 인라인으로 계산해 반환값의 필드로만 넣는다:

```typescript
const analysisHash = "0x" + createHash("sha256").update(analysisJson).digest("hex");
return { ..., analysisHash, analysisJson };
```
(`src/agent/analyzer.ts:85-107`, export 목록은 `ASSETS`, `DEFAULT_HORIZON`, `analyzeAsset`, `analyzeAll`뿐임을 grep으로 확인)

`hashAnalysis`라는 심볼은 저장소 전체에서 오직 `test/analysis.test.ts` 안에 **테스트 전용으로 재정의된 로컬 함수**로만 존재한다(`function hashAnalysis(payload: object): string { ... }`, `test/analysis.test.ts:39`). 즉 실제 운영 코드(`onchain.ts`)가 참조하는 함수는 실제로는 존재하지 않는 import이며, 유닛 테스트가 검증하는 `hashAnalysis`는 그 이름만 같을 뿐 완전히 별개의 사본이다.

이를 직접 검증하기 위해 저장소가 README에서 명시한 절차(`npm install`, `npm run typecheck` = `tsc --noEmit`)를 그대로 실행해 보았다:

```
$ npx tsc --noEmit -p tsconfig.json
src/server.ts(149,7): error TS1005: '}' expected.
```

즉 저장소에 올라온 그대로는 **`npm run typecheck`조차 통과하지 못한다**(server.ts의 별도 문법 오류). `hashAnalysis` import 문제는 이보다 더 근본적인, on-chain 기록 경로 자체의 결함이다 — 실제로 `npm run agent`(에이전트가 매시간 체인에 쓰는 커맨드)를 실행하면 `writeSignal()` 호출 시 `hashAnalysis is not a function` 런타임 에러로 실패할 것으로 판단된다. 반면 `npm test`(vitest)는 44개 테스트가 모두 통과한다:

```
✓ test/analysis.test.ts (26 tests) 50ms
✓ test/backtest.test.ts (18 tests) 150ms
Test Files  2 passed (2)
     Tests  44 passed (44)
```

이 44개 테스트는 hash 로직과 backtest 로직을 각각 고립된 상태로 검증할 뿐, `onchain.ts`의 실제 통합 경로(analyzer → onchain writer → contract call)는 전혀 커버하지 않는다 — README가 자랑하는 "44 unit tests: 26 analysis + 18 backtest"는 사실이지만, 그 테스트가 검증하는 대상과 실제로 깨져 있는 대상이 다르다.

부가적으로 계약 주석은 `analysisHash`를 "keccak256 of off-chain JSON analysis"라 설명하지만 실제 오프체인 구현은 **sha256**을 사용한다 — 기능적으로 큰 문제는 아니나(둘 다 32바이트 해시이므로 `bytes32`에 담기는 함) 문서와 구현의 불일치가 여기서도 반복된다.

### 결론: 마케팅 용어인가, 실제 구현인가

정리하면 "provenance/tamper resistance"는 **개념적으로는 실재하는 설계(계약 레벨의 append-only, resolve-once 구조)이지만, 실제로 그 해시를 만들어 체인에 올리는 코드 경로가 끊겨 있어 저장소 상태 그대로는 동작하지 않는다.** 순수 마케팅 용어(README에만 있고 코드가 전무한 경우)는 아니지만, "실제로 돌아가는 시스템"이라 보기도 어렵다 — 1인 저자가 3일 만에 만든 해커톤 제출물이라는 health signal과 일치하는 결과다. 또한 설계 자체의 한계로, 에이전트가 불리한 신호를 애초에 `recordSignal` 호출 없이 조용히 버릴 수 있는 selective-disclosure 문제는 이 구조로 막지 못한다 — 커밋된 신호들 사이의 사후 편집만 막을 뿐, "애초에 어떤 신호를 커밋할지 선택하는" 단계의 편향은 별도의 장치(예: 예정된 신호 스케줄을 사전 공개하는 방식) 없이는 방지되지 않는다.

### AIOS의 `artifact_hash`/`result_hash` 및 in-toto Statement 모델과의 비교

AIOS가 이미 채택한 방향(research_evidence_2026-09-03/ext_sigstore_intoto_slsa.md에서 다룬 in-toto Statement/SLSA 계열)과 비교하면, mantle-quant가 실제로 보여주는 것은 다음 한 가지 아이디어로 축약된다: **"결과를 알기 전에 해시를 공개 원장에 커밋하고, 결과가 나온 뒤에만 1회 정산한다"**는 commit-then-reveal 패턴이다. 이는 개념적으로 Sigstore의 transparency log(Rekor)가 제공하는 "공개적·추가전용(append-only) 타임스탬프 증명"과 근본적으로 같은 문제(사후 조작·cherry-picking 방지)를 겨냥하지만, mantle-quant는 이를 자체 스마트 컨트랙트로 재발명한 것이고, Rekor/in-toto 조합은 이미 검증된 표준 인프라로 이를 제공한다는 차이가 있다.

AIOS가 이미 선택한 in-toto Statement 모델은 (1) predicate type으로 구조화된 provenance 메타데이터, (2) materials/products 등 빌드 체인 전체의 lineage, (3) 표준 서명 방식(in-toto attestation + Sigstore 서명)을 포함해 mantle-quant의 "필드 6개짜리 struct + 단일 해시" 수준보다 훨씬 풍부하다. mantle-quant의 `analysisHash` 방식은 사실상 AIOS가 이미 갖고 있는 `artifact_hash`/`result_hash` 개념과 동일한 수준(콘텐츠 해시 하나를 남긴다)이며, 유일하게 덧붙이는 것은 "그 해시를 사람이 통제하지 않는 공개 원장(퍼블릭 블록체인)에 기록해 타임스탬프를 외부적으로 증명한다"는 배치(placement) 아이디어뿐이다. 이 아이디어 자체는 AIOS의 in-toto/SLSA 방향에서 이미 Rekor 같은 transparency log로 대체 가능한 것으로 검토되었을 가능성이 높고, mantle-quant의 구현 수준(깨진 통합 경로, 표준 스키마 부재)은 AIOS가 그대로 채용할 만한 참고 코드는 아니다.

**정직한 결론: mantle-quant는 AIOS가 아직 결정하지 않은 새로운 방향을 제시하지 못한다.** "결과 발표 전 커밋"이라는 문제의식 자체는 AIOS의 Artifact Trust Plane 논의와 정합적이지만, 해법(퍼블릭 블록체인에 해시를 얹는 것)은 이미 검토된 Sigstore/transparency-log 계열 접근의 축소판이며, 구현 완성도도 참고할 수준에 못 미친다. 얻을 수 있는 것이 있다면 "성과 클레임의 cherry-picking을 막으려면 신호/전략 산출물을 결과가 나오기 전에 append-only 로그에 남겨야 한다"는 요구사항을 다시 한 번 실증적으로 확인해 주는 사례 정도다.

---

## Part C — agenttrader (research/backtest/paper-trade 흐름)

### 저장소 개요

agenttrader는 Claude Code/Cursor/Codex 같은 코딩 에이전트가 MCP 서버를 통해 Polymarket/Kalshi 전략을 연구·백테스트·페이퍼트레이드하도록 만든 툴킷이다. README가 그리는 루프:

```
 research_markets          Find markets with price analytics and capabilities
        │ ▼
 Write strategy.py         Python class extending BaseStrategy
        │ ▼
 validate_and_backtest     Validate syntax + run backtest in one call
        │ ▼
 Evaluate metrics          Sharpe, return %, max drawdown, win rate
   ┌────┴────┐
   │ Good?   │
   │  No ────┼──▶ Edit strategy, re-run backtest
   │  Yes    │
   └────┬────┘ ▼
 start_paper_trade         Deploy to live paper trading
        │ ▼
 get_portfolio             Monitor positions and P&L
```

### 모드 분리의 실체 — "risk posture가 다른 모드"가 아니라 애초에 실거래 모드가 없음

가장 먼저 확인해야 할 것은 "research/backtest/paper-trade가 진짜 다른 risk posture를 가진 별도 모드인가, 플래그 하나 차이인가"이다. 코드를 뒤져본 결과 이 레포에는 **live(실자금) 트레이딩 실행 경로 자체가 존재하지 않는다.** `agenttrader/core/`에는 `backtest_engine.py`와 `paper_daemon.py`/`paper_daemon_runner.py`만 있고, MCP 서버(`agenttrader/mcp/server.py`)가 노출하는 도구 목록에도 `run_backtest`, `validate_and_backtest`, `start_paper_trade`, `stop_paper_trade`, `flatten_portfolio`, `get_portfolio`, `list_paper_portfolios`는 있지만 live-trade 계열 도구는 전혀 없다. 리포지토리 전체를 `live_trade|real money|real funds` 등으로 검색해도 매치가 없다.

즉 질문에 대한 답은: research(시장 데이터 조회, 실주문 없음) → backtest(과거 데이터 기반 시뮬레이션) → paper-trade(실시간 가격 기반 시뮬레이션, 역시 실주문 없음)의 3단계는 **코드상 별도 엔진(backtest_engine.py vs paper_daemon.py)으로 분리**되어 있으나, 세 단계 모두 실자금 리스크가 0이라는 점에서 "risk posture가 다른 모드"라는 질문 자체가 이 레포에는 성립하지 않는다. backtest→paper 사이의 게이트도 코드 레벨에는 없다 — `start_paper_trade` 핸들러는 전략 파일의 문법/추상클래스 유효성만 검증할 뿐, 사전에 `validate_and_backtest`가 통과했는지, 어떤 성과를 냈는지는 전혀 확인하지 않는다:

```python
if name == "start_paper_trade":
    strategy_path = Path(args["strategy_path"]).resolve()
    if not strategy_path.exists() or not strategy_path.is_file():
        return _respond(_error_payload("BadRequest", f"Strategy file not found: {args['strategy_path']}"))
    ...
    validation = validate_strategy_file(str(strategy_path))
    if not validation.get("valid", False):
        return _respond(_error_payload("StrategyValidationError", "Strategy validation failed", ...))
    ...
    strategy_hash = hashlib.sha256(strategy_path.read_bytes()).hexdigest()
    with get_session(get_engine()) as session:
        session.add(PaperPortfolio(
            id=portfolio_id, strategy_path=str(strategy_path),
            strategy_hash=strategy_hash, initial_cash=initial_cash,
            cash_balance=initial_cash, status="running", ...
        ))
```
(`agenttrader/mcp/server.py:1981-2016`)

README의 "Good? No/Yes" 분기는 **에이전트(LLM)가 스스로 판단해서 다음 도구를 호출하느냐 마느냐의 프롬프트 레벨 관행**이지, 서버가 강제하는 게이트가 아니다. 다만 여기서 눈에 띄는 세부 사항 하나는, `start_paper_trade`가 전략 파일의 `sha256` 해시(`strategy_hash`)를 계산해 `PaperPortfolio` 레코드에 함께 저장한다는 점이다 — "이 페이퍼 세션의 손익이 정확히 어떤 코드 버전에서 나온 것인가"를 재현 가능하게 묶어두는 최소한의 provenance 장치로, coinjure의 lifecycle 필드나 mantle-quant의 on-chain hash보다 소박하지만 실제로 동작하는(테스트로 뒷받침된) 기능이다.

`BaseStrategy` 추상클래스도 실제 안전장치를 코드로 강제한다 — 네트워크 접근을 상속 클래스에서 원천 차단:

```python
class BaseStrategy(ABC):
    """
    RULES:
    - Do NOT import pmxt or make network calls directly.
    - Do NOT import requests, httpx, or any networking library.
    - All data access must go through self.* methods.
    - All order placement must go through self.buy() and self.sell().
    """
```
(`agenttrader/core/base_strategy.py`)

이는 강제는 docstring 규칙일 뿐 정적 검사로 100% 보장되진 않지만, `validate_strategy_file`이 이런 규칙 위반을 잡아내는 정적 검증기라는 점은 테스트(`tests/unit/test_strategy_validator.py`, `test_strict_backtest.py`, `test_strict_integration.py`)로 뒷받침된다.

### 성숙도

1인 저자·86 커밋·32개 테스트 파일(unit 30 + integration 1 + conftest)이라는 점에서, 규모는 작지만 사이드 프로젝트치고는 완성도가 있다. `test_mcp_hardening.py`, `test_pmxt_sidecar_guard.py`(중복 프로세스 충돌 방지), `test_no_silent_synthetic.py`(데이터 소스가 조용히 합성 데이터로 대체되는 것을 막는 테스트) 같은 파일명은 실사용 중 발견된 버그를 회귀 테스트로 남긴 흔적으로 보이며, 장난감 수준은 아니다. 다만 2026-03-07 이후 6개월간 push가 없어 사실상 유지보수가 멈춘 상태다.

### 새로운 것이 있는가 (QuantDinger/LEAN/AgenticTrading/OBaI/Freqtrade 대비)

agenttrader의 핵심 패턴 — "코딩 에이전트가 `.py` 전략 파일을 직접 작성하고, MCP tool call 하나(`validate_and_backtest`)로 문법 검증과 백테스트를 동시에 수행한 뒤, 통과하면 다음 tool call(`start_paper_trade`)로 페이퍼 세션을 띄운다" — 는 이미 앞서 검토한 coinjure(CLI 기반이지만 사실상 동일하게 "LLM이 커맨드를 순서대로 호출해 전체 라이프사이클을 주도"하는 설계)와 본질적으로 같은 계열의 아이디어다. 즉 "에이전트가 코드/전략을 직접 작성하고 자체 검증 루프를 도는 하네스"라는 패턴 자체는 이번 3개 레포 중에서도 두 번(coinjure, agenttrader) 반복해서 나타나며, 이는 이미 이전 7개 레포(특히 AgenticTrading, OBaI 계열)에서 다뤄졌을 가능성이 높은 트렌드로 보인다 — agenttrader가 그 트렌드에 새로운 축을 추가한다고 보기는 어렵다.

굳이 특기할 만한 세부 구현이 있다면: (1) MCP 서버 네이티브 설계로 `validate_and_backtest`처럼 "검증+백테스트"를 원자적 tool call 하나로 묶어 에이전트의 왕복 횟수(round-trip)를 줄인 인터페이스 설계, (2) 페이퍼 포트폴리오마다 전략 파일의 `sha256`을 저장해 두는 가벼운 재현성 장치, (3) 3만 건 이상의 과거 예측시장(Polymarket/Kalshi, 2021년~) 데이터셋을 DuckDB 인덱스로 구축해 백테스트에 쓰는 데이터 엔지니어링. 이 중 (1)·(3)은 도구/데이터 엔지니어링 차원의 실용적 개선이지 AIOS 설계(Strategy Registry, Artifact Trust Plane, 승급 게이트)에 새로운 아키텍처적 시사점을 주는 수준은 아니다.

### AIOS 시사점

agenttrader는 "research/backtest/paper-trade가 서로 다른 risk posture를 갖는 3-모드 시스템"이 아니라, **애초에 실자금 리스크가 존재하지 않는 순수 시뮬레이션 도구**다 — 이 점에서 coinjure(discovery→backtest→live까지 실제로 실행 가능)와 뚜렷이 대비된다. AIOS의 "backtest→live 승급 게이트" 설계 문제에는 agenttrader가 줄 수 있는 직접적 참고가 많지 않다(애초에 그 경계를 넘는 코드가 없으므로). 다만 (a) 전략 코드의 sha256을 실행 세션에 못박아 두는 가벼운 provenance 습관, (b) `BaseStrategy`가 네트워크 접근을 원천 금지해 전략 코드와 데이터 접근을 계층적으로 분리하는 설계는, AIOS의 Strategy sandboxing/Artifact Trust Plane 논의에 작지만 실용적인 참고가 될 수 있다. 전체적으로는 "여기서도 크게 새로운 것은 없다"는 결론이 정직하다 — MCP 기반 에이전트 트레이딩 하네스라는 트렌드 자체의 재확인이지, AIOS가 아직 못 본 아키텍처는 아니다.
