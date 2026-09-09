"""task-2775 DEEPEN of task-1781 (BR-3, ADR-2026-09-06-I D2).

DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md #1781)는 원 커밋(179a8e1)이
6개 negative test(malformed-response FatalExchangeError)만 갖췄고 D2 하한
(failure-injection·수치 지연/처리량 단언·게이트/CI 적색선 회귀)에 못 미친다고
판정했다(실측 D1). 이 파일이 그 세 가지만 보강한다 —
`domestic_stock_extra_mixin.py`/`account_mixin.py`는 손대지 않는다.

1) failure-injection: 신용잔고 조회(투자자매매동향 흐름)에 네트워크 유실을
   주입해 재시도 소진 후 예외가 전파됨을, 기간별매매손익/기간별손익
   (account_mixin.py — 이 리프 이전엔 테스트가 아예 없었다)에 비JSON 응답과
   비즈니스 거부(rt_cd != "0")를 각각 주입해 부분 결과 없이 예외가 남을,
   그리고 배당공시 조회에 토큰 발급 실패를 주입해 FatalExchangeError로
   승격됨을 증명한다.
2) 수치 성능/지연 단언: 신용잔고 상위 조회(`_as_rows`/`_require` 정규화
   경로)가 원시 `_request` 왕복 대비 정규화된 배율 예산 안에 있음을
   확인한다(절대 ms 상수는 공유 CI에서 상시 적색이 되므로 쓰지 않는다 —
   task-2765/test_kis_overseas_deepen.py 선례와 동일 판단).
3) 게이트/CI 적색선 회귀: `domestic_stock_extra_mixin._require`의
   fail-closed 가드(응답에 예상 키가 없으면 조용히 빈 값을 돌려주지 않고
   예외를 낸다, §2-B 공통 규칙)를 자식 pytest 프로세스 안에서만 구세대
   `.get(key, [])` 관례로 되돌리면(프로덕션 소스는 그대로),
   `test_kis_domestic_stock_extra.py::
   test_get_credit_balance_ranking_missing_output2_raises`가 green(1 passed)에서
   red(1 failed)로 뒤집힘을 증명한다(동일 기법, test_kis_overseas_deepen.py
   선례).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.exchanges.kis.adapter import KISAdapter

_TOKEN_PATH = "/oauth2/tokenP"
_CREDIT_RANKING_PATH = "/uapi/domestic-stock/v1/ranking/credit-balance"
_PERIOD_TRADE_PROFIT_PATH = "/uapi/domestic-stock/v1/trading/inquire-period-trade-profit"
_PERIOD_PROFIT_PATH = "/uapi/domestic-stock/v1/trading/inquire-period-profit"
_DIVIDEND_PATH = "/uapi/domestic-stock/v1/ksdinfo/dividend"

_TOKEN_RESPONSE = {"access_token": "t", "access_token_token_expired": ""}


async def _instant_sleep(_seconds: float) -> None:
    """`RetryPolicy`의 백오프 대기를 건너뛴다 — 실패주입 테스트가 재시도
    4회를 실제 시간만큼 기다리지 않게 한다(오직 이 목적)."""


def _make_adapter(handler) -> KISAdapter:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter(
        "app", "secret", "12345678", "01",
        is_paper_trading=True, http_client=http_client, sleep_fn=_instant_sleep,
    )


# ---------------------------------------------------------------------------
# 1) failure-injection
# ---------------------------------------------------------------------------


async def test_credit_balance_ranking_network_drop_exhausts_retries() -> None:
    """신용잔고 상위 조회 경로에서 네트워크가 계속 끊기면
    `RetryPolicy.max_attempts`(4회)만큼 재시도하고서도 실패하면 삼키지 않고
    `RetryableExchangeError`로 전파한다."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        call_count["n"] += 1
        raise httpx.ConnectError("network down", request=request)

    adapter = _make_adapter(handler)

    with pytest.raises(RetryableExchangeError):
        await adapter.get_credit_balance_ranking()

    assert call_count["n"] == 4  # RetryPolicy 기본 max_attempts


async def test_period_trade_profit_malformed_json_raises_without_partial_result() -> None:
    """`get_period_trade_profit`(account_mixin.py, 이 리프 이전엔 테스트가
    전혀 없었다)의 응답이 JSON이 아니면(HTML 오류 페이지 등) `_classify_body`가
    단발 평가로 예외를 올린다 — 예외가 나면 output1/output2를 조립하는
    `return {"items": ..., "summary": ...}`에 절대 도달하지 못하므로 부분
    결과가 새 나갈 수 없다."""
    business_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        business_calls["n"] += 1
        return httpx.Response(200, text="<html>internal error</html>")

    adapter = _make_adapter(handler)

    with pytest.raises(RetryableExchangeError):
        await adapter.get_period_trade_profit(start_date="20260101", end_date="20260901")

    assert business_calls["n"] == 1  # 바디 레벨 실패는 재시도 없이 단발


async def test_period_profit_business_rejection_raises_retryable() -> None:
    """`get_period_profit`(account_mixin.py, 이전엔 테스트가 전혀 없었다)에
    비즈니스 거부(rt_cd != "0")를 주입하면 `_classify_body`가
    `UNKNOWN_RESPONSE`(retryable=True)로 분류해 `RetryableExchangeError`가
    나고, output1/output2 존재 여부를 확인하는 `if` 가드까지 도달하지
    못한다(그 가드에 도달했다면 이미 rt_cd == "0"이었다는 뜻)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        if request.url.path == _PERIOD_PROFIT_PATH:
            return httpx.Response(200, json={"rt_cd": "1", "msg1": "REJECTED"})
        raise AssertionError(f"예상치 못한 경로: {request.url.path}")

    adapter = _make_adapter(handler)

    with pytest.raises(RetryableExchangeError):
        await adapter.get_period_profit(start_date="20260101", end_date="20260901")


async def test_dividend_disclosures_token_fetch_failure_raises_fatal() -> None:
    """토큰 발급 자체가 실패하면(500) 배당공시 조회 경로에서도
    `_fetch_token`의 변환 규칙대로 `FatalExchangeError`가 난다 — 신용잔고/
    재무제표 전용이 아니라 이 mixin의 다른 진입점에서도 동일하게 방어됨을
    확인한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(500, json={"error": "server error"})
        raise AssertionError("토큰 발급 실패 시 배당공시 조회 요청이 나가면 안 됩니다")

    adapter = _make_adapter(handler)

    with pytest.raises(FatalExchangeError):
        await adapter.get_dividend_disclosures(symbol="005930")


# ---------------------------------------------------------------------------
# 2) 수치 성능/지연 단언(정규화된 배율 임계)
# ---------------------------------------------------------------------------


async def test_credit_balance_ranking_overhead_bounded_vs_raw_request_baseline() -> None:
    """`get_credit_balance_ranking`이 원시 `_request` 왕복 하나만 하고
    `_as_rows`/`_require`로 두 output을 정규화하는 것 말고는 추가 HTTP
    왕복이 없으므로, 총소요시간 배율은 CI 편차를 감안해도 작아야 한다.
    절대 ms 상수 대신 이 프로세스가 방금 측정한 원시 왕복 총소요시간에
    정규화한 배율을 임계로 쓴다(task-2765/test_kis_overseas_deepen.py
    선례와 동일 판단)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        if request.url.path == _CREDIT_RANKING_PATH:
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0", "msg1": "OK",
                    "output1": [{"mksc_shrn_iscd": "005930"}],
                    "output2": [{"mksc_shrn_iscd": "000660"}],
                },
            )
        raise AssertionError(f"예상치 못한 경로: {request.url.path}")

    adapter = _make_adapter(handler)
    n = 100

    # 워밍업 — 토큰 발급 지연이 표본에 섞이지 않게 미리 한 번 태운다.
    await adapter.get_credit_balance_ranking()

    baseline_started = time.perf_counter()
    for _ in range(n):
        await adapter._request(
            "GET", _CREDIT_RANKING_PATH, "FHKST17010000", params={}
        )
    baseline_elapsed = time.perf_counter() - baseline_started

    call_started = time.perf_counter()
    for _ in range(n):
        await adapter.get_credit_balance_ranking()
    call_elapsed = time.perf_counter() - call_started

    ratio = call_elapsed / baseline_elapsed
    budget_ratio = 6.0  # 둘 다 HTTP 왕복 1회씩이라 이론상 ~1배, 여유 6배
    print(
        f"\ncredit-balance-ranking overhead: n={n} baseline={baseline_elapsed * 1000:.1f}ms "
        f"call={call_elapsed * 1000:.1f}ms ratio={ratio:.2f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"get_credit_balance_ranking 오버헤드가 원시 요청 대비 {ratio:.2f}배로 "
        f"회귀했습니다(예산 {budget_ratio}배) — _as_rows/_require 정규화에 "
        "의도치 않은 무거운 연산이 섞였을 가능성."
    )


# ---------------------------------------------------------------------------
# 3) 게이트/CI 적색선 회귀 — domestic_stock_extra_mixin._require 가드 제거
# ---------------------------------------------------------------------------

_GUARD = (
    "def _require(raw: dict[str, Any], key: str, tr_id: str) -> Any:\n"
    "    if key not in raw:\n"
    '        raise FatalExchangeError(f"KIS {tr_id} 응답 파싱 실패({key} 없음): {raw}")\n'
    "    return raw[key]\n"
)
_MUTATED = (
    "def _require(raw: dict[str, Any], key: str, tr_id: str) -> Any:\n"
    "    return raw.get(key, [])\n"
)


def _plugin_source() -> str:
    return f"""\
import importlib
from pathlib import Path


def pytest_configure(config):
    module = importlib.import_module("src.exchanges.kis.domestic_stock_extra_mixin")
    source = Path(module.__file__).read_text(encoding="utf-8")
    guard = {_GUARD!r}
    assert source.count(guard) == 1
    mutated_src = source.replace(guard, {_MUTATED!r})
    mutant = compile(mutated_src, module.__file__, "exec")
    exec(mutant, module.__dict__)
"""


def test_pytest_gate_turns_red_when_require_guard_is_removed(tmp_path: Path) -> None:
    """`_require`의 fail-closed 가드(응답에 예상 키가 없으면 예외를
    낸다, §2-B 공통 규칙)를 자식 pytest 프로세스 안에서만 구세대
    `.get(key, [])` 관례로 되돌리면(프로덕션 소스는 그대로),
    `test_kis_domestic_stock_extra.py::
    test_get_credit_balance_ranking_missing_output2_raises`가 green(1 passed)
    에서 red(1 failed)로 뒤집힘을 증명한다 — 가드 없이는 output2 누락 응답이
    `FatalExchangeError`가 아니라 조용히 빈 리스트가 되어 테스트의
    `pytest.raises(FatalExchangeError)`가 만족되지 않는다."""
    target_test = (
        "tests/integration/test_kis_domestic_stock_extra.py::"
        "test_get_credit_balance_ranking_missing_output2_raises"
    )
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", target_test]
    repo_root = str(Path.cwd())
    env = dict(os.environ, PYTHONPATH=repo_root, PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8")

    baseline = subprocess.run(
        command, capture_output=True, encoding="utf-8", errors="replace",
        env=env, timeout=120, check=False,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    plugin_module_name = "_mutate_require_guard"
    plugin = tmp_path / f"{plugin_module_name}.py"
    plugin.write_text(_plugin_source(), encoding="utf-8")
    mutated_env = dict(env, PYTHONPATH=f"{repo_root}{os.pathsep}{tmp_path}")

    mutated = subprocess.run(
        [*command[:-1], "-p", plugin_module_name, command[-1]],
        capture_output=True, encoding="utf-8", errors="replace",
        env=mutated_env, timeout=120, check=False,
    )
    assert mutated.returncode != 0, mutated.stdout + mutated.stderr
    assert "1 passed" not in mutated.stdout
    assert "1 failed" in mutated.stdout
