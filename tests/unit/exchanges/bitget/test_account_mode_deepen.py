"""task-2807 DEEPEN of task-2514 (L4-31, commit ec0f4283).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-31
      (docs/audit/DEPTH_L4_BR.md — `BR-` prefixed leaves need floor D2;
      task-2514 measured D1: "No numeric latency/throughput assertion;
      simulated 40085/40099 are business/auth error codes not infra-level
      failure-injection; live-account UTA roundtrip explicitly unverified
      per commit message")

test_account_mode.py/test_uta_v3_dispatch.py already carry negative>=5 and
a gate-red reproduction (40085/40099 classify as AUTH, not the fail-closed
default UNKNOWN_RESPONSE) — D2's other two elements were missing:

1) Every failure injected so far is a business-level response (a 200 body
   carrying `"code": "40085"`); none is an actual transport-layer failure.
   `test_get_balance_survives_infra_failures_then_switches_to_unified_on_40085`
   proves ResilientTransport's retry (`httpx.ConnectError`, up to
   `RetryPolicy.max_attempts=4`, L4-11) and account_mode.account_aware_request's
   account-mode switch (L4-31) compose correctly on the same request path:
   the v2 endpoint first absorbs 2 `httpx.ConnectError`s (infra failure) and
   only then returns 40085 (business failure, triggers the switch), and the
   v3 retry after the switch absorbs 1 more `httpx.ConnectError` before
   succeeding.
2) No numeric performance assertion existed anywhere in this leaf's test
   files. `test_get_balance_classic_roundtrip_throughput_within_normalized_budget`
   asserts the measured get_balance() round-trip time (CLASSIC, no
   failures) normalized against a same-N trivial dict-construction loop
   (an absolute ms constant would be permanently red on shared CI —
   task-2773/2778/2792/2795 use the same approach).

The third gap (live-account UTA round trip) is already owned by
tests/integration/exchanges/bitget/test_live_demo_roundtrip.py::
test_get_balance_succeeds_regardless_of_account_mode (commit 6536c34c,
task-2795): marked `live_demo`, skips with a printed reason (no key values)
when `BITGET_DEMO_API_KEY`/`BITGET_DEMO_API_SECRET`/`BITGET_DEMO_API_PASSPHRASE`
are absent, and actually calls get_balance() when present. This file does
not duplicate that test.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from decimal import Decimal

import httpx

from src.exchanges.bitget.account_mode import BitgetAccountMode
from src.exchanges.bitget.adapter import BitgetAdapter


async def _no_delay_sleep(_seconds: float) -> None:
    """재시도 백오프를 없애 실패 주입 테스트가 실제 대기 없이 끝나게 한다."""
    await asyncio.sleep(0)


def _make_adapter(handler: Callable[[httpx.Request], httpx.Response]) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter(
        "key",
        "secret",
        "passphrase",
        demo_mode=True,
        http_client=http_client,
        sleep_fn=_no_delay_sleep,
    )


def _envelope(code: str, data: object) -> dict:
    return {"code": code, "msg": "test", "requestTime": 1, "data": data}


# ---------------------------------------------------------------------------
# 1) infra-level failure-injection + 계정모드 전환 조합
# ---------------------------------------------------------------------------


async def test_get_balance_survives_infra_failures_then_switches_to_unified_on_40085() -> None:
    """v2가 인프라 실패(`httpx.ConnectError`) 2회 뒤 40085(비즈니스 실패)를
    반환하고, 전환된 v3도 인프라 실패 1회 뒤 성공한다."""
    v2_calls = 0
    v3_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal v2_calls, v3_calls
        if request.url.path == "/api/v2/spot/account/assets":
            v2_calls += 1
            if v2_calls <= 2:
                raise httpx.ConnectError("simulated network failure", request=request)
            return httpx.Response(200, json=_envelope("40085", {}))
        assert request.url.path == "/api/v3/account/assets"
        v3_calls += 1
        if v3_calls == 1:
            raise httpx.ConnectError("simulated network failure", request=request)
        row = {"coin": "usdt", "available": "10", "locked": "0"}
        return httpx.Response(200, json=_envelope("00000", {"assets": [row]}))

    adapter = _make_adapter(handler)
    assert adapter.account_mode is BitgetAccountMode.CLASSIC

    balances = await adapter.get_balance()

    assert v2_calls == 3  # ConnectError x2 + 40085(바디 검증은 단발 평가, 재시도 없이 즉시 실패)
    assert v3_calls == 2  # ConnectError x1 + 성공
    assert adapter.account_mode is BitgetAccountMode.UNIFIED
    assert balances[0].asset == "USDT"
    assert balances[0].available == Decimal("10")


# ---------------------------------------------------------------------------
# 2) 수치 성능 단언 — get_balance() CLASSIC 왕복(정규화된 배율 임계)
# ---------------------------------------------------------------------------


async def test_get_balance_classic_roundtrip_throughput_within_normalized_budget() -> None:
    """실패 없는 CLASSIC 경로 get_balance() 왕복의 실측 소요시간을, 동일 N
    크기의 trivial dict 생성 루프(같은 프로세스, 같은 측정 시점) 대비
    정규화한 배율로 단언한다 — 절대 ms 상수는 공유 CI에서 상시 적색을
    낳으므로 쓰지 않는다."""
    n = 100
    repeats = 5

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/account/assets"
        row = {"coin": "usdt", "available": "10", "frozen": "0", "locked": "0"}
        return httpx.Response(200, json=_envelope("00000", [row]))

    adapter = _make_adapter(handler)

    async def _get_balance_once() -> None:
        balances = await adapter.get_balance()
        assert balances[0].asset == "USDT"

    # 워밍업 — import/JIT 관련 1회성 비용이 표본에 섞이지 않게 한다.
    await _get_balance_once()

    roundtrip_times: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        for _ in range(n):
            await _get_balance_once()
        roundtrip_times.append(time.perf_counter() - start)
    roundtrip_seconds = min(roundtrip_times)

    baseline_times: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        baseline = [{"coin": "usdt", "available": str(i), "locked": "0"} for i in range(n)]
        baseline_times.append(time.perf_counter() - start)
    assert len(baseline) == n
    baseline_seconds = min(baseline_times)

    assert baseline_seconds > 0.0
    ratio = roundtrip_seconds / baseline_seconds
    # httpx MockTransport 왕복(HMAC 서명 계산 포함) 오버헤드가 trivial dict
    # 생성보다 수천 배 커서(task-2792/2795 동일 패턴, 로컬 실측 유사
    # 자릿수) 여유를 넉넉히 잡는다.
    budget_ratio = 30000.0
    print(
        f"\nget_balance CLASSIC roundtrip throughput: n={n} repeats={repeats} "
        f"baseline={baseline_seconds * 1000:.2f}ms roundtrip={roundtrip_seconds * 1000:.2f}ms "
        f"ratio={ratio:.1f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"get_balance() CLASSIC 왕복이 trivial dict 생성 루프 대비 {ratio:.1f}배로 "
        f"회귀했습니다(예산 {budget_ratio}배) — account_aware_request 경로에 의도치 "
        "않은 무거운 연산이 섞였을 가능성."
    )
