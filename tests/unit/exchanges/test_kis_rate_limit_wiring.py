"""BR-2b(task-3458) — KIS 호출 한도 실배선 검증.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §3.2, ADR-2026-09-06-I D4.

리뷰 3306 REJECT 근거: (1) `_KISTokenTransportMixin._request()`가 쓰는
`ResilientTransport`가 `rate_limiter=None`으로 만들어져, `rate_profile.py`가
어떤 값을 선언하든 어떤 TR 호출도 클라이언트 쪽에서 실제로 한도를 강제받지
않았다. (2) `build_token_bucket`이 호출마다 새 버킷을 만들어, 호출부가
캐싱을 깜빡하면 그룹당 상한이 무의미해졌다.

아래 테스트는 `rate_profile.py`가 아니라 `oauth_client.py`/`adapter.py`의
실제 `_request()` 경로(모의 서버, `httpx.MockTransport`)를 통해 TR을 20회
연속 호출해 (a) 실제 `asyncio.sleep`으로 대기가 발생하고(sleep 모킹 없음,
review-exchange 체크리스트 8항), (b) 그 사실이 관측되며, (c) 이 배선이
빠지면(REJECT 3306 원래 결함) 테스트가 실제로 red가 됨을 증명한다(게이트
적색 재현, mutation 기법은 test_kis_rate_profile.py와 동일)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from src.exchanges.kis import rate_profile
from src.exchanges.kis.adapter import _KISHTTPClient
from src.exchanges.kis.rate_profile import KisAccountType, RateLimitSpec

_TR_ID = "FHKST01010100"  # domestic_stock 그룹 (test_kis_rate_profile.py와 동일 TR)


@pytest.fixture(autouse=True)
def _reset_bucket_registry() -> None:
    rate_profile.reset_token_bucket_registry_for_test()
    yield
    rate_profile.reset_token_bucket_registry_for_test()


def _paper_client(handler: Callable[[httpx.Request], httpx.Response]) -> _KISHTTPClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return _KISHTTPClient(
        "app", "secret", "12345678", "01", is_paper_trading=True, http_client=http_client
    )


@pytest.mark.perf
async def test_20_consecutive_tr_calls_through_real_request_path_absorb_rate_limit_as_real_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실호출 경로 통합 테스트(DoD) — 모의 서버에 동일 TR을 20회 연속
    호출한다. 실제 시간 경과로 한도 대기가 최소 1회 관측돼야 한다(sleep
    모킹 없음). 테스트 속도를 위해 domestic_stock 그룹의 PAPER 프로파일만
    빠른(그러나 실측 가능한) 값으로 한시 교체한다 — 프로덕션 파일은
    건드리지 않는다."""
    monkeypatch.setitem(
        rate_profile._PROFILE[KisAccountType.PAPER],
        "domestic_stock",
        RateLimitSpec(rate_per_sec=200.0, burst=3.0, verified="ESTIMATED"),
    )

    tr_call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(
                200,
                json={
                    "access_token": "tok-1",
                    "access_token_token_expired": "2099-01-01 00:00:00",
                },
            )
        nonlocal tr_call_count
        tr_call_count += 1
        return httpx.Response(200, json={"rt_cd": "0"})

    client = _paper_client(handler)
    started = time.perf_counter()
    for _ in range(20):
        await client._request("GET", "/uapi/domestic-stock/v1/quotations/inquire-price", _TR_ID)
    elapsed = time.perf_counter() - started

    assert tr_call_count == 20
    assert client.rate_limit_wait_count >= 1, (
        "20회 연속 호출 중 burst(3)를 넘는 17건은 최소 1회 이상 대기를 "
        "흡수해야 한다 — 0이면 rate_limiter가 실제로 배선되지 않은 것"
    )
    # burst 3개를 뺀 17건이 200/s로 순차 리필돼야 하므로 최소 17/200=0.085s는
    # 실제로(모킹 없이) 흘러야 한다 — 이 하한 자체가 "sleep을 모킹해 즉시
    # 통과시키지 않았다"는 증거다.
    assert elapsed >= 0.05, f"elapsed={elapsed:.4f}s -- 실제 대기가 발생하지 않음"


def test_pytest_gate_turns_red_when_rate_limiter_injection_is_removed(
    tmp_path: Path,
) -> None:
    """게이트 적색 재현(DoD) — `oauth_client.py`에서 `rate_limiter=bucket`
    주입을 제거해(REJECT 3306 원래 결함 재현) 위 실호출 경로 테스트가
    green -> red로 뒤집힘을 증명한다. 프로덕션 파일은 그대로 두고 자식
    프로세스의 모듈 객체만 변조한다(test_kis_rate_profile.py의 동일 기법
    재사용)."""
    test_copy = tmp_path / "test_kis_rate_limit_wiring_copy.py"
    test_copy.write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\nasyncio_mode = auto\n", encoding="utf-8")
    target = (
        f"{test_copy}::"
        "test_20_consecutive_tr_calls_through_real_request_path_absorb_rate_limit_as_real_wait"
    )
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-c",
        str(config),
        "--confcutdir",
        str(tmp_path),
        target,
    ]
    env = dict(os.environ, PYTHONPATH=str(Path.cwd()), PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8")

    baseline = subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
        check=False,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    (tmp_path / "conftest.py").write_text(
        "import importlib\n"
        "from pathlib import Path\n"
        "name = 'src.exchanges.kis.oauth_client'\n"
        "module = importlib.import_module(name)\n"
        "source = Path(module.__file__).read_text(encoding='utf-8')\n"
        "guard = 'rate_limiter=bucket, sleep=self._sleep_fn'\n"
        "assert source.count(guard) == 1, source\n"
        "mutated_src = source.replace(guard, 'sleep=self._sleep_fn')\n"
        "mutant = compile(mutated_src, module.__file__, 'exec')\n"
        "exec(mutant, module.__dict__)\n",
        encoding="utf-8",
    )
    mutated = subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
        check=False,
    )
    assert mutated.returncode == 1, mutated.stdout + mutated.stderr
    assert "1 failed" in mutated.stdout
