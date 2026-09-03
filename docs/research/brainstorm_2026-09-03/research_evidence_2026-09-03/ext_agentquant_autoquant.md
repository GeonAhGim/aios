# 외부 레포 코드 레벨 분석 — AgentQuant / autoquant

조사일: 2026-09-03. 두 레포 모두 `git clone --depth 1` 후 `git fetch --unshallow`로 전체 히스토리 확보. 이전에 분석한 QuantDinger/LEAN/Freqtrade보다 훨씬 작고 검증되지 않은 개인 레포이므로, 먼저 기본 health signal(최근 커밋, 기여자 수, 테스트 존재 여부, 실제 동작 여부)을 확인한 뒤 본 조사 항목(leakage/causality/OOS, bounded mutation + ledger, code-level hard-fail, overfitting 통계)을 검증했다.

---

## 사전 Health Check 요약

| 항목 | AgentQuant | autoquant |
|---|---|---|
| 커밋 수 (전체 히스토리) | 71 | 3 |
| 기여자 (이메일 기준) | 1명 (onepunchmonk, 3개 이메일 별칭) + dependabot | 1명 (rock@miromind.ai) |
| 활동 기간 | 2025-08-12 ~ 2026-08-28 (약 1년, 현재 시점 기준 6일 전까지 활동) | 2026-03-31 ~ 2026-04-01 (단 2일) |
| 최근 커밋 메시지 | "docs: Add comprehensive ROADMAP with 30+ future directions" | "feat: v2 engine — walk-forward validation, T+1 execution, professional A-share rules" |
| 테스트 | `tests/` 14개 파일, `pytest` 실행 시 **63/63 통과** (57.86s) | `tests/` 5개 파일, `pytest` 실행 시 **21/24 통과**, 3개는 `baostock`/`akshare` 미설치로 인한 데이터 소스 실패(로직 결함 아님) |
| 실제 동작 여부 | 대체로 동작. 단, `experiments/walk_forward.py` 등 구버전 스크립트 일부는 `src.agent.langchain_planner`처럼 현재 존재하지 않는 모듈을 import해 실행 불가(죽은 실험 스크립트) | 핵심 3개 파일(`backtest.py`, `strategy.py`, `prepare.py`)은 자급자족 가능한 단일 개발자용 엔진. `results.tsv`(실험 이력 파일)는 저장소에 **존재하지 않음** — "에이전트가 실행 중에 생성"하는 템플릿 산출물이라 저장소 자체에는 실제 실험 이력이 0건 |

두 레포 모두 "1인 프로젝트"라는 점에서 이전에 본 QuantDinger/LEAN급 커뮤니티 프로젝트와는 신뢰 수준이 다르다. AgentQuant는 1년간 71커밋으로 실제로 반복 개발된 흔적이 뚜렷하고 테스트가 전부 통과하는 반면, autoquant는 단 3커밋·2일짜리 스캐폴딩이며 이후 약 5개월간(2026-04-01 → 2026-09-03) 추가 커밋이 없다 — "완성된 시스템"이라기보다 "설계 문서 + 엔진 뼈대"에 가깝다. 이 차이를 각 파트의 성숙도 평가에 반영한다.

---

# Part A — AgentQuant (leakage / causality / OOS 검증)

### 1. Leakage/causality/OOS 검증 로직 — 실제 코드가 있는가

결론: **있다.** README/DESIGN.md의 "look-ahead bias guards enforced at the backtest engine level" 주장은 실제 구현과 일치한다.

**(a) Warmup/lookback guard — 실제로 백테스트 경로에 연결됨**

`src/features/lookback_guard.py:19-46`
```python
def enforce_lookback(min_periods: int):
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            if isinstance(result, pd.Series):
                n_valid = result.notna().sum()
                if n_valid < min_periods:
                    raise InsufficientWarmupError(
                        f"{func.__name__} produced only {n_valid} valid values "
                        f"but requires {min_periods}. Provide more historical data."
                    )
            return result
        return wrapper
    return decorator
```
이 데코레이터는 `src/features/engine.py:69, 107`에서 실제 feature 함수(RSI 등, `min_periods=14`)에 적용되어 있고, `WarmupEnforcer`는 `src/backtest/runner.py:21,67,166`에서 `run_backtest` 실행 경로에 직접 인스턴스화되어 사용된다(`config.backtest.min_warmup_periods` 로부터). 즉 "문서에만 있는 주장"이 아니라 실제 실행 경로에 배선된 code다.

**(b) Causal ordering (T+1 신호 지연)**

`src/backtest/runner.py:95`
```python
strat_ret = daily_ret * signal.shift(1).fillna(0) - costs
```
`src/backtest/simple_backtest.py:63`도 동일 패턴(`signal.shift(1)`)을 사용 — 신호는 계산된 다음 봉에서만 체결되도록 강제되어 있어 lookahead 없는 인과적 순서를 코드 레벨로 보장한다.

**(c) Walk-forward OOS — 있지만 스크립트 산발적/일부는 고장**

`experiments/walk_forward.py:40-172`는 실제 chronological train/test 슬라이싱, train-window에서 파라미터 선택 후 test-window에서 재평가하는 완전한 walk-forward 루틴을 갖고 있다(warmup 처리 포함). 그러나 이 파일은 `from src.agent.langchain_planner import generate_strategy_proposals`를 import하는데, 현재 `src/agent/` 디렉터리에는 `langchain_planner.py`가 존재하지 않는다(현재는 `base_planner.py`/`proposal_generator.py` 체계로 리팩터링됨). 즉 이 walk-forward 스크립트는 **현재 코드베이스 기준으로 실행 불가능한 죽은 실험 스크립트**다. `docs/PAPER_DRAFT.md`, `DESIGN.md`도 이 구버전 스크립트를 인용하고 있어, 문서와 실제 실행 가능한 코드 사이에 버전 드리프트가 있다.

`src/agent/tools/evals.py:151-184`의 `checkpoint_replay()`는 이름과 docstring상 "하니스 버전들을 held-out 데이터에 재생하여 실제 일반화 여부 측정"을 목표로 하지만, 본문은:
```python
return {
    "total_checkpoints": 0,
    "improved_training": 0,
    "improved_held_out": 0,
    "hurt_transfer": 0.0,
    "generalization_rate": 0.0,
    "note": "Checkpoint replay requires harness version history",
}
```
전부 0을 반환하는 **미구현 스텁**이다. 이는 "OOS 검증"이라는 명칭의 함수가 실제로는 아직 구현되지 않은 대표 사례다.

### 2. Hard-fail / rejection 메커니즘 — 코드로 계산되는가, LLM 판단인가

혼합형이다. 진짜 code-level hard-fail과, 이름만 hard-fail이고 실제로는 아무것도 막지 못하는 부분이 공존한다.

**진짜 code-level hard-fail (파라미터 검증)** — `src/agent/proposal_generator.py` `ProposalValidator.validate()`:
```python
if fw is None or sw is None:
    return None
...
if fw <= 0 or sw <= 0 or fw >= sw:
    return None
```
`src/agent/swarm/critic_agent.py:44-56`의 `CriticAgent.review()`도 동일 계열 — trend_following의 `short<medium<long` 순서 위반, 중복 proposal을 LLM 판단이 아닌 코드 비교로 reject한다(`rejected.append(...)`).

**이름은 hard-fail이지만 실제로는 게이트가 아닌 부분** — `src/agent/agent_graph.py`의 `reflect_node`(129-166행)는 `min_acceptable_sharpe` 임계값과 비교해 "ACCEPTING" / "Retrying" / "Accepting best available"를 로그로 남기지만, 이 판정은 **오직 "루프를 한 번 더 돌릴지"만 결정**한다. 실제 저장은 무조건 실행되는 `store_node`(261-266행)에서 일어난다:
```python
def store_node(state: AgentState) -> AgentState:
    """Persist best result to strategy memory."""
    best = state.get("best_result")
    if best is None:
        ...
        return state
    ...
    run_id = memory.store(result)   # sharpe 임계값과 무관하게 항상 저장
```
`reflect_node`가 "Sharpe 0.1 < threshold 0.8이지만 max_iterations 도달, best available 채택"이라고 판단해도 `store_node`는 그 결과를 그대로 `StrategyMemory`/`AlphaStore`/`NLAMemoryStore`에 영구 저장한다. **결과적으로 min_acceptable_sharpe는 "저장을 막는 게이트"가 아니라 "재시도를 유발하는 소프트 트리거"에 불과하다** — AIOS의 `hard_fail_reasons`가 항상 빈 튜플이라 게이트가 실제로 실패할 수 없는 것과 동일한 클래스의 버그다.

또한 `src/agent/tools/evals.py`의 `run_benchmark_to_assess_quality`(`passed = oos_sharpe >= min_acceptable_sharpe and avg_drawdown <= min_acceptable_dd`)는 계산 자체는 코드로 되어 있어 진짜지만, `src/agent/tools/registry.py:212-216, 300-320`에서 이는 **LLM이 선택적으로 호출할 수 있는 tool**로만 등록되어 있다. 에이전트 루프(`agent_graph.py`)의 필수 단계가 아니라, tool-calling 에이전트가 "판단상 필요하다고 느끼면" 호출하는 방식이라 강제성이 없다.

### 3. Overfitting 통계 — 실제 구현 vs 언급만

**실제 구현**: `src/backtest/metrics.py` `PerformanceMetrics.bootstrap_sharpe()`(69-93행)는 moving-block bootstrap으로 Sharpe의 5th percentile을 계산한다:
```python
def bootstrap_sharpe(returns, n=200, pct=5, block_size=20):
    """5th percentile Sharpe from a moving-block bootstrap (penalizes lucky
    results). Uses overlapping blocks ... so autocorrelation/regime
    structure in the return series is preserved..."""
```
IID 리샘플이 아니라 block bootstrap을 명시적으로 선택한 이유까지 주석에 설명되어 있어 설계 의도가 뚜렷하다. `agent_graph.py`의 `backtest_node`도 `bootstrap_sharpe_p5`를 결과 dict에 포함시켜 순위 결정에 활용한다(`results.sort(key=lambda x: x.get("sharpe", ...))`는 sharpe 기준이지만 p5 값도 함께 저장·전달됨).

**언급만 있고 구현이 없는 것**: PBO(Probability of Backtest Overfitting), Deflated Sharpe Ratio(DSR), parameter sensitivity surface는 코드베이스 전체에서 **grep 결과 0건**이다. `generalization_gap`(`evals.py:100-110`)이라는 이름의 메트릭이 있지만, 실제로는 `AlphaStore`에 미리 채워진 `candidate.metadata["generalization_gap"]`가 있을 때만 평균을 내는 수동적 집계일 뿐, train-vs-holdout을 스스로 계산하는 로직은 없다(값이 없으면 그냥 0.5 반환).

### AIOS 시사점 (AgentQuant)

- **채택할 만한 패턴**: `enforce_lookback` 데코레이터 + `WarmupEnforcer`처럼, "충분한 워밍업 데이터가 없으면 feature 계산 자체가 예외를 던진다"는 방식은 AIOS의 `foundation/validation`에 곧바로 이식 가능하다. 특히 데코레이터 형태라 개별 feature 함수마다 최소 침습적으로 적용할 수 있다. `signal.shift(1)` 패턴 역시 causal ordering을 "검증 단계에서 사후 확인"하는 대신 "애초에 look-ahead가 불가능하도록 백테스트 엔진 레벨에 강제"하는 더 근본적인 접근이라 AIOS 백테스트 실행기에도 원칙적으로 채택할 가치가 있다. `bootstrap_sharpe`의 block bootstrap 구현(및 IID 대신 block을 쓴 이유 주석)도 그대로 참고할 만하다.
- **AIOS와 동일 클래스의 결함**: `reflect_node`의 임계값 체크가 `store_node`의 무조건 저장을 막지 못하는 구조는 AIOS `hard_fail_reasons`가 항상 빈 튜플인 것과 본질적으로 같은 실패 패턴이다 — "게이트를 계산은 하지만 그 결과를 실제 분기(저장 차단)에 연결하지 않음". AIOS Validation & Experiment Plane을 설계할 때, "게이트 계산 함수가 존재한다"와 "그 계산 결과가 실제로 쓰기 경로를 차단한다"를 반드시 별도로 테스트해야 한다는 반면교사 사례로 인용할 수 있다. 마찬가지로 `run_benchmark_to_assess_quality`가 LLM이 호출 여부를 스스로 선택하는 tool로만 노출된 것도, "게이트가 프롬프트 안에만 존재"하는 OBaI류 패턴과 같은 계열이다.
- **결측 영역**: PBO/DSR/parameter-sensitivity는 AgentQuant에도 전혀 없다. 이 부분은 AIOS가 이 레포를 참고해도 채울 수 없고, 별도로 구현해야 한다.

---

# Part B — autoquant (bounded mutation + experiment ledger)

### 사전 확인: 레포가 이름/설명이 암시하는 것을 실제로 포함하는가

부분적으로만 그렇다. 핵심 엔진 코드(`backtest.py`, `strategy.py`, `prepare.py`, `simulate.py`)는 실재하고 상당히 정교하지만, "실험 이력 관리(experiment ledger)"의 실제 데이터인 `results.tsv`는 저장소에 **존재하지 않는다** — README/CLAUDE.md/program.md 모두 "Agent가 실행 중 results.tsv를 생성한다"고 명시하므로, 이 레포 자체는 이력 파일의 **스키마와 절차만 정의한 템플릿**이며 실제 축적된 실험 이력 데이터는 0건이다. 또한 3개 커밋·2일간의 개발 이후 5개월간 활동이 없어, "agent가 반복 수정한" 실제 러닝 기록(커밋 로그, `.git`에 남았을 실험 브랜치 등)도 이 클론에는 없다 — 즉 이 레포가 보여주는 것은 "bounded mutation을 위한 하네스 설계"이지, "실제로 수백 번 반복해서 검증된 결과"가 아니다.

### 2. Bounded mutation 경계 — 실제로 파일/필드를 제한하는가

**코드로 강제되는 것은 strategy.py "내용"에 대한 AST 검사뿐, "어느 파일을 건드릴 수 있는가"는 강제되지 않는다.**

`backtest.py:28-59`:
```python
FORBIDDEN_IMPORTS = {"os", "subprocess", "socket", "requests", "urllib", "shutil", "pathlib"}
FORBIDDEN_CALLS = {"exec", "eval", "__import__", "compile"}

def safety_check(strategy_path: str) -> tuple[bool, str]:
    ...
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_module = alias.name.split(".")[0]
                if root_module in FORBIDDEN_IMPORTS:
                    return False, f"Forbidden import: {alias.name}"
```
`backtest.py:560-564`에서 이 결과를 실제로 `sys.exit(1)`로 프로세스 중단에 연결한다:
```python
is_safe, reason = safety_check(str(strategy_path))
if not is_safe:
    print(f"SAFETY CHECK FAILED: {reason}")
    sys.exit(1)
```
이것은 진짜 code-level hard-fail이며, `tests/test_backtest.py`의 `test_safety_check_blocks_os_import`, `test_safety_check_blocks_subprocess` 등으로 실제 테스트도 되어 있다.

그러나 "Agent는 strategy.py만 수정해야 한다"는 경계 자체는 `.claude/settings.json` 같은 파일 권한 설정, git hook, 파일시스템 lock 등 **어떤 코드로도 강제되지 않는다** — 저장소 내 `.claude` 디렉터리나 permission 설정 파일은 존재하지 않는다(확인: `find . -iname "*.claude*"` 결과 없음). 이 경계는 오직 `CLAUDE.md`와 `program.md`의 자연어 지시("Only modify strategy.py", "Do not modify prepare.py, backtest.py, simulate.py")로만 존재하며, LLM 에이전트가 그 지시를 준수하는 것을 전제로 한다. 즉 "파일 경계"는 prompt-level convention이고, "코드 내용 경계"(AST 금지어)만 code-level이다.

### 실험 이력(ledger) — 스키마는 코드/문서에 있지만 append는 LLM 책임

`program.md:57-59`가 정의하는 스키마:
```
commit	score	score_val	score_test	status	description	timestamp
```
`docs/scoring.md:123-144`가 명시하는 keep/drop 로직:
```
if new_score_val > best_score_val:
    git commit -m "description of change"
    record status = "keep"
    best_score_val = new_score_val
else:
    git checkout -- strategy.py
    record status = "drop"
```
이 pseudocode는 **문서에만 존재**한다 — `backtest.py`/`simulate.py`/`prepare.py` 전체를 grep해도 `git commit`, `git checkout`, `subprocess`를 통한 git 호출은 0건이다(`FORBIDDEN_IMPORTS`에 `subprocess`가 포함되어 있어 strategy.py 쪽에서도 애초에 못 쓴다). 즉 "새 score_val이 이전보다 좋으면 커밋, 아니면 되돌리고 results.tsv에 append"하는 실제 판정·기록 로직은 **LLM 에이전트(Claude Code)가 매 iteration마다 스스로 비교하고 git 명령을 실행하는 것에 전적으로 의존**한다. 코드가 계산해주는 것은 `score`, `score_val`, `score_test` 세 숫자뿐이고, 그 숫자를 놓고 "keep or drop"을 판단해 실행하는 것은 에이전트의 판단(및 순응)이다.

이 구조가 갖는 필드는 재구성 가능성 관점에서는 나쁘지 않다 — `commit`(재현 가능한 코드 스냅샷), `score/score_val/score_test`(train/val/test 세 값을 모두 남겨 held-out 오염 여부를 사후 감사 가능), `status`(keep/drop/crash), `description`(가설), `timestamp`. 다만 이 스키마가 실제로 append-only하게 지켜지는지, LLM이 불리한 실험을 누락하거나 사후에 수정하지 않는지를 강제하는 코드는 없다 — TSV 파일이므로 append든 rewrite든 구분할 수단이 없고, git commit 이력과 대조 검증하는 별도 스크립트도 없다.

### 3. Hard-fail / rejection 메커니즘 — 코드로 계산되는가

**혼합형, 그러나 AgentQuant보다 한 겹 더 얕다.**

진짜 code-level hard-fail: `safety_check()` → `sys.exit(1)` (위 참조). 이것은 명확히 코드가 판단하고 코드가 차단한다 — LLM의 협조 여부와 무관하게 안전하지 않은 import가 있으면 백테스트 자체가 실행되지 않는다.

반면 "전략의 질" 관련 hard-fail(과적합 의심 시 거부)은 전혀 코드화되어 있지 않다. `compute_score()`(`backtest.py:148-175`)는 거래 수가 `MIN_TRADES=20` 미만이면 `score *= trade_count / MIN_TRADES`로 점수를 선형 할인하지만, 이는 "reject"가 아니라 "감점"이며, 여전히 양의 score_val을 받아 `git commit`될 수 있다(그 판정도 LLM 몫). walk-forward의 폴드 수가 부족하거나(`if len(val_d) < 60: break`) fold 자체가 하나도 안 나오면 단순 80/20 fallback으로 조용히 전환되며(`if not fold_scores:` 블록, 510-520행) 이 경우도 에러 없이 계속 진행된다 — degraded validation이 로그 없이 silent fallback되는 지점이다.

### 4. Overfitting 특화 통계

**실제 구현**: walk-forward(3-year train / 1-year validation, multi-fold), 마지막 10% held-out test set 분리, `score_test`는 "절대 의사결정에 쓰지 않는다"는 원칙이 문서·CLAUDE.md·backtest.py 출력 문구(`"[held-out, DO NOT use for decisions]"`) 모두에서 일관되게 강조된다. 이는 최소 형태의 train/val/test 3-way split이며, PBO나 DSR 같은 정식 통계는 아니지만 "held-out 오염 방지"라는 목적은 실질적으로 코드에 구현되어 있다. `MIN_TRADES` 할인도 저-샘플 전략(통계적으로 신뢰 불가능한 결과)에 대한 원시적 형태의 overfitting 페널티다.

**언급만 있는 것**: PBO, Deflated Sharpe Ratio는 `program.md`의 "Avoid overfitting: simple strategies beat complex ones"라는 한 줄 지침 외에는 어디에도 등장하지 않는다 — 이는 수치 계산이 아니라 LLM에게 주는 편향 유도 문구에 가깝다.

### AIOS 시사점 (autoquant)

- **채택할 만한 패턴**: `score` / `score_val` / `score_test`를 구조적으로 분리하고, `score_test`를 "의사결정에 쓰지 않는다"는 원칙을 코드 출력·문서·에이전트 지시 세 군데 모두에 중복 명시한 방식은 AIOS Experiment Plane에 그대로 적용할 가치가 있다 — held-out 오염을 막는 가장 값싼 방법은 정교한 통계가 아니라 "그 값을 아예 의사결결정 경로에서 분리해서 보여주는 것"이다. `safety_check()`처럼 AST 파싱으로 금지된 import/call을 코드 레벨에서 차단하고 `sys.exit(1)`로 실제 실행을 막는 패턴도, AIOS가 에이전트에게 전략/설정 파일 수정 권한을 줄 때 "파일 내용에 대한 정적 검사 + 프로세스 중단"으로 이식 가능하다. `MIN_TRADES` 미만 시 점수를 선형 할인하는 방식도 저샘플 전략에 대한 간단하지만 실효성 있는 방어선이다.
- **AIOS가 반복하면 안 되는 패턴**: "경계 준수"와 "keep/drop 판정 후 git commit/revert"를 전부 LLM 에이전트의 자발적 순응에 위임하고 이를 강제하는 코드가 전혀 없는 구조는, AIOS Validation & Experiment Plane이 그대로 베끼면 안 되는 안티패턴이다. 이는 "게이트가 프롬프트에만 존재"하는 문제의 또 다른 변주다 — 여기서는 게이트 계산(`score_val`)은 진짜지만, 그 계산 결과를 실제 행동(커밋 vs 되돌리기, ledger append)으로 전환하는 절차 자체가 code가 아니라 md 파일의 지시문이다. AIOS라면 최소한 "score_val 비교 → git 액션 → ledger append"를 하나의 검증 가능한 스크립트/함수로 만들어 LLM이 그 스크립트를 호출하도록 강제하고, 스크립트의 실행 여부·반환값을 감사 로그로 남겨야 한다. 또한 fold 부족 시 silent fallback(80/20)되는 부분처럼, "정식 walk-forward가 실패하면 조용히 약한 검증으로 전환"하는 패턴이 있는지 AIOS 자체 파이프라인에서도 점검할 필요가 있다.
- **레포 성숙도상의 한계**: 이 레포는 3커밋·5개월간 미활동 상태의 초기 스캐폴딩이며 `results.tsv` 실 데이터가 전혀 없다는 점에서, "실제로 검증된 실험 이력 관리 시스템"이라기보다 "그런 시스템을 만들기 위한 설계 초안"에 가깝다. AIOS가 참고할 때는 "이 패턴이 실전에서 작동함이 증명됐다"가 아니라 "이 설계가 이론적으로 합리적이다" 정도로만 신뢰도를 매겨야 한다.

---

## 종합 성숙도 평가

| | AgentQuant | autoquant |
|---|---|---|
| **성숙도 등급** | Beta — 1인 개발이지만 1년/71커밋의 실사용 흔적, 63/63 테스트 통과, 핵심 leakage guard가 실행 경로에 실제 배선됨. 다만 문서와 코드 간 버전 드리프트(죽은 langchain_planner 참조), 핵심 게이트(min_acceptable_sharpe)가 저장을 막지 못하는 구조적 결함 존재 | Early prototype / scaffold — 엔진 코드(`backtest.py` walk-forward, T+1, AST safety)는 정교하지만 3커밋·2일 개발 후 5개월간 방치, 실제 실험 이력 데이터 0건, "bounded mutation"의 파일 경계가 code 강제가 아닌 prompt 준수에 전적으로 의존 |
| **AIOS 대비 결론** | AIOS의 `hard_fail_reasons` 상시-빈-튜플 버그와 정확히 같은 클래스의 결함(게이트 계산 ≠ 게이트 강제)이 이 레포에도 존재 — "S+ 등급 레포도 같은 함정에 빠진다"는 것을 보여주는 유용한 반면교사. 동시에 lookback guard/causal shift/block-bootstrap Sharpe는 AIOS에 그대로 가져다 쓸 수 있는 실동 코드 | 핵심 아이디어(train/val/test 3-way 분리, AST 기반 정적 안전 검사)는 유효하지만, "경계 강제"와 "이력 기록"이라는 이 레포의 핵심 셀링 포인트 자체가 코드가 아니라 md 지시문 수준에 머물러 있어, AIOS가 그대로 채택하면 동일한 "프롬프트에만 있는 게이트" 문제를 물려받게 됨 |

두 레포 모두 "리서치 방향은 옳지만 마지막 한 걸음(계산된 게이트를 실제 분기/권한에 강제로 연결하는 것)이 코드화되지 않았다"는 동일한 패턴을 보인다. AIOS Validation & Experiment Plane을 설계할 때 이 마지막 한 걸음 — 게이트 계산 결과가 실제로 쓰기 경로(저장/커밋/배포)를 차단하는지에 대한 단위 테스트 — 를 최우선 요구사항으로 못박을 근거로 이 두 사례를 함께 인용할 수 있다.
