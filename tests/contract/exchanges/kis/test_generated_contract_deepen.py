"""task-2786 DEEPEN of task-1930 (BR-13, ADR-2026-09-06-I D7).

DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md #1930)는 원 커밋(126e271)의
계약 테스트가 D2 하한에 못 미친다고 판정했다(실측 D1). 근거 두 가지:

1. 성능/처리량/지연 수치 단언이 어디에도 없음.
2. failure-injection이 응답-변조(mutation) 한 종류뿐이고, 인프라 레벨
   실패(크래시/네트워크/DB급 장애)를 주입하는 테스트가 없음
   (`test_contract_helper_detects_response_tampering`는 생성 메서드가 응답을
   조용히 바꿔치기하는 결함만 잡지, 전송 계층이 끊기거나 응답이 아예
   JSON이 아니거나 거래소가 비즈니스 사유로 거부하는 시나리오는 다루지
   않는다).

이 파일이 그 두 가지만 보강한다 — `src/exchanges/kis/generated/*.py`,
`tests/fixtures/kis/generated_cases.py`의 discovery 로직(subprocess 뮤테이션
대상 제외), `test_generated_contract.py`는 손대지 않는다.

1) failure-injection: 생성 메서드 진입점(`getattr(adapter, case.method_name)`)을
   실제로 호출해 네트워크 유실(재시도 소진)·비JSON 응답·비즈니스 거부·토큰
   발급 실패를 주입한다. 대표 케이스 하나는 params 스타일, 다른 하나는
   body 스타일로 골라 두 인자 방식 모두 fail-closed로 전파됨을 확인한다
   (원 계약 테스트는 canned 200 응답만 다뤘다 — 이 네 경로 전부 그 밖의
   어떤 테스트에도 실패주입 커버리지가 없었다, grep 확인 2026-09-10).
2) 수치 성능 단언: 절대 ms 상수 대신, 같은 프로세스 안에서 즉시 측정한
   원시 `_request` 왕복 총소요시간에 정규화한 배율 임계를 쓴다
   (task-2765/test_submit_order_failure_injection.py,
   task-2772/test_kis_overseas_deepen.py 선례와 동일 판단).
3) 게이트/CI 적색선 회귀: `discover_generated_cases()`의 glob 패턴을 자식
   pytest 프로세스 안에서만 좁히면(생성기가 청크 파일 일부를 조용히
   빠뜨리는 결함을 흉내냄, 프로덕션 소스는 그대로), 원 파일의
   `test_discovers_all_generated_methods`(케이스 수 회귀 가드)가
   green(1 passed)에서 red(1 failed)로 뒤집힘을 증명한다(동일 기법,
   test_paper_drop_injection.py/task-1605 선례).
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.exchanges.kis.adapter import REAL_BASE_URL, KISAdapter
from tests.fixtures.kis.generated_cases import (
    GeneratedCase,
    discover_generated_cases,
    load_reference_rows,
)

_REFERENCE = load_reference_rows()
_CASES = discover_generated_cases()
_REST_CASES = [c for c in _CASES if c.kind == "rest"]

_TOKEN_PATH = "/oauth2/tokenP"
_TOKEN_RESPONSE = {
    "access_token": "tok-br13-deepen-fixture",
    "access_token_token_expired": "2099-01-01 00:00:00",
}

_PARAMS_CASE = next(c for c in _REST_CASES if c.arg_style == "params")
_BODY_CASE = next(c for c in _REST_CASES if c.arg_style == "body")


async def _instant_sleep(_seconds: float) -> None:
    """`RetryPolicy`의 백오프 대기를 건너뛴다 — 실패주입 테스트가 재시도
    4회를 실제 시간만큼 기다리지 않게 한다(오직 이 목적, 기존
    test_kis_capability_matrix_deepen.py와 동일 기법)."""


def _make_adapter(handler: Any) -> KISAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url=REAL_BASE_URL, transport=transport)
    # is_paper_trading=True + tr_id 치환 무력화 -- 원 계약 테스트(_make_real_adapter)와
    # 동일 이유(task-2018/task-1975 require_paper_sandbox 하드가드 통과 +
    # BR-11 tr_id 문자 그대로 일치 유지).
    adapter = KISAdapter(
        "app",
        "secret",
        "12345678",
        "01",
        is_paper_trading=True,
        http_client=client,
        sleep_fn=_instant_sleep,
    )
    adapter._resolve_tr_id = lambda tr_id: tr_id  # type: ignore[method-assign]
    return adapter


def _sample_params(case: GeneratedCase) -> dict[str, str]:
    row = _REFERENCE[case.tr_id]
    return {p["name"]: f"V_{p['name']}" for p in row["params"]}


# ---------------------------------------------------------------------------
# 1) failure-injection -- 생성 메서드 진입점을 통한 인프라 레벨 실패 주입
# ---------------------------------------------------------------------------


async def test_generated_method_network_drop_exhausts_retries_then_raises_retryable() -> None:
    """params 스타일 대표 케이스 -- 엔드포인트가 계속 네트워크로 끊기면
    `RetryPolicy.max_attempts`(4회)만큼 재시도하고서도 실패하면 삼키지 않고
    `RetryableExchangeError`로 전파한다. 원 계약 테스트는 canned 200 응답만
    다뤄 이 경로를 전혀 검증하지 않았다."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        call_count["n"] += 1
        raise httpx.ConnectError("network down", request=request)

    adapter = _make_adapter(handler)
    try:
        method = getattr(adapter, _PARAMS_CASE.method_name)
        with pytest.raises(RetryableExchangeError):
            await method(_sample_params(_PARAMS_CASE))
    finally:
        await adapter.aclose()

    assert call_count["n"] == 4  # RetryPolicy 기본 max_attempts


async def test_generated_method_malformed_json_raises_retryable_without_retry() -> None:
    """body 스타일 대표 케이스 -- 응답이 JSON이 아니면(HTML 오류 페이지 등)
    바디 레벨 실패는 재시도 없이 단발로 `RetryableExchangeError`를 올린다.
    생성 메서드가 이를 조용히 삼키고 빈 dict 등을 반환하지 않는지 확인한다."""
    business_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        business_calls["n"] += 1
        return httpx.Response(200, text="<html>internal error</html>")

    adapter = _make_adapter(handler)
    try:
        method = getattr(adapter, _BODY_CASE.method_name)
        with pytest.raises(RetryableExchangeError):
            await method(_sample_params(_BODY_CASE))
    finally:
        await adapter.aclose()

    assert business_calls["n"] == 1  # 바디 레벨 실패는 재시도 없이 단발


async def test_generated_method_business_rejection_raises_retryable_not_returned_as_data() -> None:
    """거래소가 비즈니스 사유로 거부하면(rt_cd != "0") 그 응답 바디가 마치
    정상 데이터인 양 그대로 반환(왕복)되지 않고 `RetryableExchangeError`가
    난다 -- 원 계약 테스트의 "응답 항등 왕복" 단언은 rt_cd == "0" 성공
    케이스만 다뤘으므로, 실패 응답이 같은 방식으로 조용히 통과하지 않음을
    이 테스트가 별도로 증명한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        return httpx.Response(200, json={"rt_cd": "1", "msg1": "REJECTED-BY-VENUE"})

    adapter = _make_adapter(handler)
    try:
        method = getattr(adapter, _PARAMS_CASE.method_name)
        with pytest.raises(RetryableExchangeError):
            await method(_sample_params(_PARAMS_CASE))
    finally:
        await adapter.aclose()


async def test_generated_method_token_fetch_failure_raises_fatal_before_any_business_call() -> None:
    """토큰 발급 자체가 실패하면(500) 생성 메서드가 어떤 자산군/청크에
    속하든 동일하게 `FatalExchangeError`가 나고, 실제 TR 엔드포인트에는
    어떤 요청도 나가지 않는다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(500, json={"error": "server error"})
        raise AssertionError("토큰 발급 실패 시 TR 요청이 나가면 안 됩니다")

    adapter = _make_adapter(handler)
    try:
        method = getattr(adapter, _BODY_CASE.method_name)
        with pytest.raises(FatalExchangeError):
            await method(_sample_params(_BODY_CASE))
    finally:
        await adapter.aclose()


# ---------------------------------------------------------------------------
# 2) 수치 성능 단언(정규화된 배율 임계)
# ---------------------------------------------------------------------------


def _fast_handler(case: GeneratedCase, captured: list[httpx.Request]) -> Any:
    row = _REFERENCE[case.tr_id]
    containers = row["response"]["containers"] or ["output"]
    canned_response = {
        "rt_cd": "0",
        "msg1": "OK",
        **{c: {"marker": f"{case.tr_id}-fixture"} for c in containers},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        captured.append(request)
        return httpx.Response(200, json=canned_response)

    return handler


async def test_generated_method_call_overhead_bounded_vs_raw_request_baseline() -> None:
    """생성 메서드는 얇은 래퍼일 뿐이라(`return await self._request(...)`,
    src/exchanges/kis/generated/*_mixin.py 전수 확인) 원시 `_request` 왕복
    하나만 하는 것과 같은 수의 HTTP 왕복(1회)을 쓴다 -- 총소요시간 배율은
    CI 편차를 감안해도 작아야 한다. 절대 ms 상수 대신 이 프로세스가 방금
    측정한 원시 왕복 총소요시간에 정규화한 배율을 임계로 쓴다
    (test_kis_overseas_deepen.py 선례와 동일 판단 -- 공유 CI 환경에서 절대
    임계는 상시 적색을 낳는다)."""
    case = _PARAMS_CASE
    row = _REFERENCE[case.tr_id]
    captured: list[httpx.Request] = []
    adapter = _make_adapter(_fast_handler(case, captured))
    n = 100
    params = _sample_params(case)

    # 워밍업 -- 토큰 발급 지연이 표본에 섞이지 않게 미리 한 번 태운다.
    method = getattr(adapter, case.method_name)
    await method(params)
    captured.clear()

    assert row["path"] is not None and case.http_method is not None
    baseline_started = time.perf_counter()
    for _ in range(n):
        if case.arg_style == "params":
            await adapter._request(case.http_method, row["path"], case.tr_id, params=params)
        else:
            await adapter._request(case.http_method, row["path"], case.tr_id, body=params)
    baseline_elapsed = time.perf_counter() - baseline_started

    dispatch_started = time.perf_counter()
    for _ in range(n):
        await method(params)
    dispatch_elapsed = time.perf_counter() - dispatch_started

    ratio = dispatch_elapsed / baseline_elapsed
    budget_ratio = 6.0  # 둘 다 HTTP 왕복 1회씩이라 이론상 ~1배, 여유 6배
    print(
        f"\ngenerated method call overhead ({case.tr_id}): n={n} "
        f"baseline={baseline_elapsed * 1000:.1f}ms dispatch={dispatch_elapsed * 1000:.1f}ms "
        f"ratio={ratio:.2f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"{case.tr_id}: 생성 메서드 호출 오버헤드가 원시 요청 대비 {ratio:.2f}배로 "
        f"회귀했습니다(예산 {budget_ratio}배) -- 생성기가 얇은 래퍼 이상의 무거운 "
        "연산을 끼워 넣었을 가능성."
    )


# ---------------------------------------------------------------------------
# 3) 게이트/CI 적색선 회귀 -- discover_generated_cases() glob 패턴 좁히기
# ---------------------------------------------------------------------------

_GUARD = '    for path in sorted(GENERATED_DIR.glob("*_mixin.py")):\n'
_MUTATED = '    for path in sorted(GENERATED_DIR.glob("domestic_stock_*_mixin.py")):\n'


def test_discovery_glob_source_matches_expected_snippet() -> None:
    """뮤테이션 테스트가 문자열 치환에 의존하므로, 픽스처 소스가 예상한
    형태 그대로인지 먼저 확인한다(소스가 바뀌면 아래 subprocess 테스트가
    무의미하게 항상 통과하는 것을 방지)."""
    module = importlib.import_module("tests.fixtures.kis.generated_cases")
    assert module.__file__ is not None
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert source.count(_GUARD) == 1


def _plugin_source() -> str:
    return f"""\
import importlib
from pathlib import Path


def pytest_configure(config):
    module = importlib.import_module("tests.fixtures.kis.generated_cases")
    source = Path(module.__file__).read_text(encoding="utf-8")
    guard = {_GUARD!r}
    assert source.count(guard) == 1
    mutated_src = source.replace(guard, {_MUTATED!r})
    mutant = compile(mutated_src, module.__file__, "exec")
    exec(mutant, module.__dict__)
"""


def test_pytest_gate_turns_red_when_generated_case_discovery_glob_is_narrowed(
    tmp_path: Path,
) -> None:
    """`discover_generated_cases()`의 glob 패턴을 자식 pytest 프로세스
    안에서만 좁히면(생성기가 청크 파일 일부를 조용히 빠뜨리는 결함을
    흉내냄, 프로덕션 소스는 그대로),
    `test_generated_contract.py::test_discovers_all_generated_methods`
    (`assert len(_CASES) >= 289`)가 green(1 passed)에서 red(1 failed)로
    뒤집힌다 -- BR-12 생성 당시 실제로 이 가드가 지키려던 시나리오(청크
    파일 일부가 조용히 스킵돼 케이스 수가 조용히 줄어드는 회귀)를
    재현한다(동일 기법, test_paper_drop_injection.py/task-1605 선례)."""
    target_test = (
        "tests/contract/exchanges/kis/test_generated_contract.py::"
        "test_discovers_all_generated_methods"
    )
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", target_test]
    repo_root = str(Path.cwd())
    env = dict(os.environ, PYTHONPATH=repo_root, PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8")

    baseline = subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
        check=False,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    plugin_module_name = "_mutate_generated_case_discovery_glob"
    plugin = tmp_path / f"{plugin_module_name}.py"
    plugin.write_text(_plugin_source(), encoding="utf-8")
    mutated_env = dict(env, PYTHONPATH=f"{repo_root}{os.pathsep}{tmp_path}")

    mutated = subprocess.run(
        [*command[:-1], "-p", plugin_module_name, command[-1]],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=mutated_env,
        timeout=120,
        check=False,
    )
    assert mutated.returncode != 0, mutated.stdout + mutated.stderr
    assert "1 passed" not in mutated.stdout
    assert "1 failed" in mutated.stdout
