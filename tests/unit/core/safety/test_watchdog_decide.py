import ast
import decimal
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import pytest

from src.core.safety.split_brain import Diagnosis, FailureDomain
from src.core.safety.watchdog import WatchdogAction, WatchdogSnapshot, decide

_REPO_ROOT = Path(__file__).resolve().parents[4]


def _snapshot(loss_pct="8", unresponsive_sec=0.0, exchange_healthy=True) -> WatchdogSnapshot:
    return WatchdogSnapshot(
        loss_pct=Decimal(loss_pct),
        unresponsive_sec=unresponsive_sec,
        exchange_healthy=exchange_healthy,
    )


def _failure_domain(diagnosis: Diagnosis, *, db_ok: bool = True) -> FailureDomain:
    return FailureDomain(db_ok=db_ok, exchange_ok=True, main_process_ok=True, diagnosis=diagnosis)


def test_no_failure_domain_liquidates_as_before():
    decision = decide(_snapshot(), market_wide_correlated=True, failure_domain=None)
    assert decision.action == WatchdogAction.LIQUIDATE


def test_normal_failure_domain_does_not_downgrade_liquidate():
    decision = decide(
        _snapshot(),
        market_wide_correlated=True,
        failure_domain=_failure_domain(Diagnosis.NORMAL),
    )
    assert decision.action == WatchdogAction.LIQUIDATE


def test_db_isolated_failure_downgrades_liquidate_to_halt():
    decision = decide(
        _snapshot(),
        market_wide_correlated=True,
        failure_domain=_failure_domain(Diagnosis.DB_ISOLATED_FAILURE, db_ok=False),
    )
    assert decision.action == WatchdogAction.HALT
    assert decision.reason == "db_isolated_liquidate_downgraded"


def test_db_isolated_failure_does_not_affect_already_halt_decision():
    decision = decide(
        _snapshot(),
        market_wide_correlated=False,
        failure_domain=_failure_domain(Diagnosis.DB_ISOLATED_FAILURE, db_ok=False),
    )
    assert decision.action == WatchdogAction.HALT
    assert decision.reason == "isolated_loss_suspected_manipulation"


def test_db_isolated_failure_does_not_affect_normal_decision():
    decision = decide(
        _snapshot(loss_pct="1"),
        market_wide_correlated=None,
        failure_domain=_failure_domain(Diagnosis.DB_ISOLATED_FAILURE, db_ok=False),
    )
    assert decision.action == WatchdogAction.NORMAL


# --- 경계값(negative) 테스트 ---------------------------------------------------


def test_loss_exactly_at_threshold_triggers_evaluation():
    """negative — loss_pct가 임계값과 정확히 같으면(>=) 이미 초과로 취급돼야
    한다(임계값보다 살짝 낮을 때만 NORMAL)."""
    decision = decide(
        _snapshot(loss_pct="7.0"),
        market_wide_correlated=False,
        failure_domain=None,
        loss_threshold_pct=Decimal("7.0"),
    )
    assert decision.action == WatchdogAction.HALT


def test_loss_just_below_threshold_is_normal():
    """negative — 임계값보다 아주 조금 낮으면 여전히 NORMAL이어야 한다."""
    decision = decide(
        _snapshot(loss_pct="6.999", unresponsive_sec=0.0),
        market_wide_correlated=None,
        failure_domain=None,
        loss_threshold_pct=Decimal("7.0"),
    )
    assert decision.action == WatchdogAction.NORMAL


def test_unresponsive_and_high_loss_prioritizes_loss_branch_not_unresponsive_reason():
    """negative — 응답불능(unresponsive_sec 초과)과 고손실이 동시에 발생하면,
    "main_process_unresponsive" 사유가 아니라 손실 판정(및 그에 딸린
    market_wide_correlated/failure_domain 로직)이 우선해야 한다. `decide()`의
    첫 분기는 `loss_pct < loss_threshold_pct`일 때만 unresponsive 사유를
    반환하므로, 고손실이면 이 분기를 건너뛴다."""
    decision = decide(
        _snapshot(loss_pct="9", unresponsive_sec=999.0),
        market_wide_correlated=True,
        failure_domain=None,
    )
    assert decision.action == WatchdogAction.LIQUIDATE
    assert decision.reason == "market_wide_correlated_loss"


def test_unresponsive_below_threshold_with_low_loss_is_normal():
    """negative — 응답불능도 아니고(임계 미만) 손실도 낮으면 NORMAL."""
    decision = decide(
        _snapshot(loss_pct="0", unresponsive_sec=1.0),
        market_wide_correlated=None,
        failure_domain=None,
    )
    assert decision.action == WatchdogAction.NORMAL


# --- 실패 주입 ------------------------------------------------------------


def test_decide_fails_loud_on_corrupted_nan_loss_pct():
    """실패 주입 — equity 계산 경로가 손상돼 loss_pct가 Decimal('NaN')으로
    들어오면(예: peak/current 계산이 깨진 경우) 조용히 NORMAL이나 임의의
    판정으로 새지 않고 예외로 시끄럽게 실패해야 한다 — fail-closed 원칙
    (§R3, 상수/결손 입력을 암묵 통과시키지 않는다)."""
    snapshot = _snapshot(loss_pct="0")
    snapshot = snapshot.model_copy(update={"loss_pct": Decimal("NaN")})
    with pytest.raises(decimal.InvalidOperation):
        decide(snapshot, market_wide_correlated=None, failure_domain=None)


# --- 성능 단언 --------------------------------------------------------------


def test_decide_perf_bound_for_bulk_calls():
    """성능 단언 — decide()는 필드 비교 몇 개뿐인 O(1) 순수 함수다.
    watchdog_process 루프는 5초 주기로 한 번 호출하지만, 누군가 실수로
    루프·I/O를 끼워넣는 회귀를 잡기 위해 대량 반복 호출 시간 상한을 못박는다."""
    snapshot = _snapshot(loss_pct="9")
    domain = _failure_domain(Diagnosis.NORMAL)
    start = time.perf_counter()
    for _ in range(50_000):
        decide(snapshot, market_wide_correlated=True, failure_domain=domain)
    elapsed = time.perf_counter() - start
    # pydantic 모델 생성 오버헤드(WatchdogDecision 인스턴스화)가 대부분이라
    # 상한을 넉넉히 잡는다 — 목적은 정확한 절대치가 아니라 회귀(예: 실수로
    # 끼워든 반복문·I/O로 인한 자릿수 단위 급증)를 잡는 것이다.
    assert elapsed < 5.0, (
        f"50,000회 decide() 호출에 {elapsed:.2f}s — O(1) 순수 함수치고 과도하게 느림"
    )


# --- 다중 인스턴스(동시 실행) 증명 -------------------------------------------


def test_decide_consistent_across_concurrent_threads():
    """다중 인스턴스 증거 — decide()는 순수 함수라 공유 가변 상태가 없어야
    한다. 여러 스레드(여러 watchdog 배포/프로세스를 근사)가 동시에 같은
    입력으로 호출해도 항상 같은 결과가 나와야 한다."""
    snapshot = _snapshot(loss_pct="9")
    domain = _failure_domain(Diagnosis.DB_ISOLATED_FAILURE, db_ok=False)

    def _call(_: int) -> WatchdogAction:
        return decide(snapshot, market_wide_correlated=True, failure_domain=domain).action

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_call, range(200)))

    assert all(r == WatchdogAction.HALT for r in results)


# --- 게이트 적색 재현: 프로덕션 호출부 배선(RTF-03) --------------------------
#
# DEPTH 감사(task-2721)가 지적한 [중대] 결함 — src/watchdog_process.py의
# run_one_cycle이 decide()를 호출할 때 failure_domain을 실제로 넘기지 않아
# (옵션 인자가 항상 기본값 None으로 고정되는, task-1806 P0-B와 동일 클래스의
# 배선 결함) DB_ISOLATED 강등 로직이 실행 경로에서 도달 불가했다. 이 스캐너는
# 그 호출 지점의 형태를 AST로 직접 검사해 회귀를 잡는다 — xfail 없는 하드
# 게이트다.


def _decide_call_keyword_sets(source: str) -> list[set[str]]:
    """소스에서 `decide(...)` 호출을 찾아, 리터럴 None이 아닌 값으로 전달된
    키워드 인자 이름 집합의 리스트를 반환한다(호출마다 하나씩)."""
    tree = ast.parse(source)
    calls: list[set[str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "decide"
        ):
            keywords = {
                kw.arg
                for kw in node.keywords
                if kw.arg is not None
                and not (isinstance(kw.value, ast.Constant) and kw.value.value is None)
            }
            calls.append(keywords)
    return calls


def test_scanner_flags_call_site_missing_failure_domain_before_fix():
    """회귀 재현(before) — task-2133 원 커밋 당시 실제 호출부 형태
    (`decide(snapshot, market_wide_correlated=None)`)를 그대로 스캔하면
    failure_domain이 전달되지 않았음을 잡아야 한다."""
    source = "decision = decide(snapshot, market_wide_correlated=None)\n"
    calls = _decide_call_keyword_sets(source)
    assert calls == [set()]


def test_scanner_flags_literal_none_failure_domain_as_missing():
    """negative — 호출 지점에서 `failure_domain=None`을 명시적으로 박아
    넘겨도(타입상 허용되는 값이라 시그니처 검사만으로는 못 잡는 우회) 실질적
    으로는 안 넘긴 것과 같다 — 리터럴 None은 '전달됨'으로 카운트하지 않는다."""
    source = "decide(snapshot, market_wide_correlated=None, failure_domain=None)\n"
    calls = _decide_call_keyword_sets(source)
    assert calls == [set()]


def test_scanner_accepts_call_site_with_failure_domain_after_fix():
    """회귀 재현(after) — 실제 값을 담은 변수를 failure_domain으로 넘기면
    '전달됨'으로 잡혀야 한다."""
    source = (
        "decision = decide(\n"
        "    snapshot, market_wide_correlated=None, failure_domain=failure_domain\n"
        ")\n"
    )
    calls = _decide_call_keyword_sets(source)
    assert any("failure_domain" in kws for kws in calls)


def test_watchdog_process_actually_passes_failure_domain_to_decide():
    """하드 게이트 — xfail 없음. `src/watchdog_process.py`의 실제 소스를 스캔해
    decide() 호출부가 failure_domain을 실제 값으로 전달하는지 확인한다.
    위 before/after 테스트가 스캐너 자체의 검출 능력을 fixture로 이미
    증명했으므로, 이 테스트가 실패하면 스캐너 결함이 아니라 RTF-03 배선
    결함의 재발을 가리키는 정확한 신호다."""
    source = (_REPO_ROOT / "src" / "watchdog_process.py").read_text(encoding="utf-8")
    calls = _decide_call_keyword_sets(source)
    assert calls, "watchdog_process.py에서 decide() 호출을 찾지 못함"
    assert any("failure_domain" in kws for kws in calls), (
        "decide() 호출부가 failure_domain을 실제 값으로 전달하지 않음 — RTF-03 재발"
    )
