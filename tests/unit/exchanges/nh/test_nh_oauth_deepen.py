"""task-2782 DEEPEN of task-1788 (BR-10, docs/audit/DEPTH_L4_BR.md #1788,
ADR-2026-09-06-I D6, commit cf6174f).

DEPTH 감사(task-2722)는 원 커밋이 negative test 4개와 시뮬레이션된
`httpx.ConnectError` failure-injection(tests/unit/exchanges/nh/
test_adapter_oauth_wiring.py)을 갖췄지만 D2 하한에 필요한 두 가지가
없다고 판정했다: (1) 수치 latency/throughput 성능 단언, (2) 게이트/CI
적색선 회귀 테스트. 이 파일이 그 두 가지만 보강한다 — `adapter.py`/
`common/oauth_http.py`는 손대지 않는다.

1) 수치 throughput 성능 단언: `NHAccountMixin.get_balance`가 다건(2000행)
   응답을 파싱하는 실측 소요시간을, 같은 프로세스에서 측정한 동일 N
   크기의 trivial Decimal 생성 루프에 정규화한 배율로 단언한다(절대 ms
   상수 대신 — task-2778/test_domestic_futureoption_deepen.py와 동일
   판단, 공유 CI 환경에서 절대 임계는 상시 적색을 낳는다).
2) 게이트/CI 적색선 회귀: `_NHHTTPClient._classify_body`의 "완료" 메시지
   폴백 분기(SDK 관례상 `rsp_cd`가 알려진 성공코드 목록에 없어도
   `rsp_msg`에 "완료"가 포함되면 성공 취급, 02e 스펙 §2)를 자식 pytest
   프로세스 안에서만 제거하면(프로덕션 소스는 그대로),
   `tests/integration/test_nh_adapter.py::
   test_request_treats_wanryo_message_as_success_even_with_unlisted_code`가
   green(1 passed)에서 red(1 failed)로 뒤집힘을 증명한다(동일 기법,
   test_kis_ws_overseas_deepen.py/task-2777 선례).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

import httpx

from src.exchanges.nh.adapter import NHAdapter

TOKEN_RESPONSE = {"access_token": "tok-1", "expires_in": 86400}
_BALANCE_PATH = "/krstock/inquiry/v1/balance"


def _make_adapter(handler) -> NHAdapter:
    client = httpx.AsyncClient(
        base_url="https://moapi.nhplug.com:8443", transport=httpx.MockTransport(handler)
    )
    return NHAdapter("appkey", "appsecret", "1234567890", http_client=client)


# ---------------------------------------------------------------------------
# 1) 수치 throughput 성능 단언(정규화된 배율 임계)
# ---------------------------------------------------------------------------


def _synthetic_holdings(n: int) -> list[dict[str, str]]:
    return [
        {"iem_cd": f"00{i:05d}", "itg_bnc_qty": "10", "rsdl_qty": "8"}
        for i in range(n)
    ]


def _make_balance_adapter(n: int) -> NHAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=TOKEN_RESPONSE)
        payload = {
            "rsp_cd": "00000",
            "rsp_msg": "정상처리완료",
            "Output_0": {"dca": "1000000"},
            "Output_1": _synthetic_holdings(n),
        }
        return httpx.Response(200, json=payload)

    return _make_adapter(handler)


async def test_balance_parsing_throughput_within_normalized_budget() -> None:
    """`get_balance`가 2000행 응답을 파싱하는 실측 소요시간을 동일 N
    크기의 trivial Decimal 생성 루프(같은 프로세스, 같은 측정 시점) 대비
    정규화한 배율로 단언한다 — 절대 ms 상수는 공유 CI에서 상시 적색을
    낳으므로(task-2778 선례) 쓰지 않는다."""
    n = 2000
    repeats = 5
    adapter = _make_balance_adapter(n)

    # 워밍업 — import/JIT 관련 1회성 비용이 표본에 섞이지 않게 한다.
    warmup = await adapter.get_balance()
    assert len(warmup) == n + 1  # 보유종목 n건 + KRW 예수금 1건

    balance_times: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        balances = await adapter.get_balance()
        balance_times.append(time.perf_counter() - start)
    assert len(balances) == n + 1
    balance_seconds = min(balance_times)

    baseline_times: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        baseline = [Decimal(f"00{i:05d}") for i in range(n)]
        baseline_times.append(time.perf_counter() - start)
    assert len(baseline) == n
    baseline_seconds = min(baseline_times)

    assert baseline_seconds > 0.0
    ratio = balance_seconds / baseline_seconds
    # httpx MockTransport JSON 직렬화/역직렬화 오버헤드 포함, task-2778과 동일
    # 여유 폭 판단.
    budget_ratio = 60.0
    print(
        f"\nnh balance parse throughput: n={n} repeats={repeats} "
        f"baseline={baseline_seconds * 1000:.2f}ms balance={balance_seconds * 1000:.2f}ms "
        f"ratio={ratio:.1f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"get_balance가 trivial Decimal 생성 루프 대비 {ratio:.1f}배로 회귀했습니다"
        f"(예산 {budget_ratio}배) — 응답 파싱 경로에 의도치 않은 무거운 연산이 섞였을 "
        "가능성."
    )


# ---------------------------------------------------------------------------
# 2) 게이트/CI 적색선 회귀 — _classify_body의 "완료" 메시지 폴백 제거
# ---------------------------------------------------------------------------

_GUARD = '        if rsp_cd in _SUCCESS_CODES or "완료" in rsp_msg:\n'
_MUTATED = '        if rsp_cd in _SUCCESS_CODES:\n'


def _plugin_source() -> str:
    return f"""\
import importlib
from pathlib import Path


def pytest_configure(config):
    module = importlib.import_module("src.exchanges.nh.adapter")
    source = Path(module.__file__).read_text(encoding="utf-8")
    guard = {_GUARD!r}
    assert source.count(guard) == 1
    mutated_src = source.replace(guard, {_MUTATED!r})
    mutant = compile(mutated_src, module.__file__, "exec")
    exec(mutant, module.__dict__)
"""


def test_pytest_gate_turns_red_when_wanryo_fallback_guard_is_removed(
    tmp_path: Path,
) -> None:
    """`_classify_body`의 "완료" 메시지 폴백 분기(SDK 관례상 `rsp_cd`가
    알려진 성공코드 목록에 없어도 `rsp_msg`에 "완료"가 포함되면 성공
    취급, 02e 스펙 §2)를 자식 pytest 프로세스 안에서만 제거하면(프로덕션
    소스는 그대로), `test_nh_adapter.py::
    test_request_treats_wanryo_message_as_success_even_with_unlisted_code`가
    green(1 passed)에서 red(1 failed)로 뒤집힘을 증명한다 — 이 폴백이
    없으면 NH가 실제로 쓰는 비표준 성공코드(SDK 소스로 확인된 값 이외의
    코드에 "완료" 메시지가 붙는 경우)가 조용히 실패로 오분류돼 정상 체결/
    조회가 재시도 루프로 새는 회귀를 막는다."""
    target_test = (
        "tests/integration/test_nh_adapter.py::"
        "test_request_treats_wanryo_message_as_success_even_with_unlisted_code"
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

    plugin_module_name = "_mutate_nh_wanryo_fallback_guard"
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
