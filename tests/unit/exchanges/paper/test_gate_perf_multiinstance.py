"""L4-22 paper 모델 D2/D3 보강 — DEPTH 감사(task-2722, 리프 task-1534/task-2758)
지적 대응.

원 커밋(f6746f0)의 test_fill_model.py/test_purity.py는 negative>=3과 전역
random 오염 실패주입은 있었으나(docs/audit/DEPTH_L4_BR.md #1534) 다음
세 가지가 없었다:
  D2: 수치 성능/지연 단언 1건, 게이트/CI 적색선 회귀 테스트 1건
  D3: 다중 인스턴스/리플레이 적대적 증명 1건
이 파일이 그 세 가지만 보강한다. 모델 코드는 손대지 않는다.
"""
from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from itertools import product

import pytest

from src.data.models.base import Currency
from src.data.models.trading import OrderSide
from src.exchanges.paper.fee_model import FeeModel
from src.exchanges.paper.fill_model import FillModel, SimFill
from src.exchanges.paper.latency_model import LatencyModel, LatencyOutcome
from tests.unit.exchanges.paper.helpers import FixedRng, make_book, make_order

# ---- D2: 수치 성능/지연 단언 (CI 게이트) -----------------------------------
#
# task-1038/1521 선례(tests/performance/oms/*)는 공유 Postgres 왕복시간의
# 환경 편차 때문에 절대시간을 print(비차단)로만 남기고 왕복 수를 게이트로
# 썼다. 이 리프는 DB/네트워크가 전혀 없는 순수 CPU(Decimal) 계산이라 그
# 편차가 없다 — 절대 벽시계 시간을 직접 CI 게이트로 써도 안전하다.


@pytest.mark.perf
def test_fill_fee_latency_pipeline_throughput_budget() -> None:
    fill_model = FillModel(Decimal("5"), Decimal("1"), 0.0, Decimal("10"))
    fee_model = FeeModel(maker_bps=Decimal("2"), taker_bps=Decimal("6"), fee_currency=Currency.USDT)
    latency_model = LatencyModel(ack_ms_p50=50.0, ack_ms_p99=500.0, drop_response_prob=0.0)
    order = make_order(quantity="1")
    book = make_book()
    adv = Decimal("1000")

    n = 5000
    started = time.perf_counter()
    for _ in range(n):
        fills = fill_model.simulate(order, book, adv, FixedRng([]))
        assert fills
        fee_model.fee(fills[0])
        latency_model.sample(FixedRng([0.5]))
    elapsed_s = time.perf_counter() - started
    per_call_us = (elapsed_s / n) * 1_000_000
    budget_s = 3.0  # 여유 600us/call — 순수 Decimal 산술 기준 수백 배 여유

    print(
        f"\nfill+fee+latency pipeline: n={n} total={elapsed_s * 1000:.1f}ms "
        f"per_call={per_call_us:.1f}us (budget={budget_s}s)"
    )
    assert elapsed_s < budget_s, (
        f"paper 모델 파이프라인 처리량 회귀: {n}회에 {elapsed_s:.2f}s (예산 {budget_s}s) — "
        "순수 함수인데 이 정도로 느려지면 알고리즘 회귀입니다."
    )


# ---- D2: 게이트/CI 적색선 회귀 테스트 --------------------------------------
#
# 모듈 docstring의 핵심 안전 불변식("시뮬은 낙관 금지")을 넓은 파라미터
# 행렬로 고정한다. 이 불변식이 깨지면(예: 슬리피지 부호 반전, 라운딩 방향
# 실수) 페이퍼 트레이딩 성과가 실거래보다 낙관적으로 부풀려지고, 이는 곧
# 실거래 배포 판단에 쓰이는 지표 자체가 거짓이 된다는 뜻이다 — 반드시
# 병합을 막아야 하는 회귀이므로 CI 적색선으로 고정한다.

_SIDES = [OrderSide.BUY, OrderSide.SELL]
_SPREADS = [Decimal("0"), Decimal("5"), Decimal("50")]
_IMPACTS = [Decimal("0"), Decimal("1"), Decimal("20")]
_QTYS = ["0.001", "1", "500"]
_TICKS = [Decimal("0"), Decimal("0.1"), Decimal("1")]


@pytest.mark.parametrize(
    "side,spread,impact,qty,tick",
    list(product(_SIDES, _SPREADS, _IMPACTS, _QTYS, _TICKS)),
)
def test_gate_red_line_fill_never_more_favorable_than_reference(
    side: OrderSide, spread: Decimal, impact: Decimal, qty: str, tick: Decimal
) -> None:
    model = FillModel(spread, impact, 0.0, Decimal("10"))
    order = make_order(side, quantity=qty)
    fills = model.simulate(order, make_book(), Decimal("1000"), FixedRng([]), tick=tick)
    assert len(fills) == 1
    fill = fills[0]
    if side == OrderSide.BUY:
        assert fill.price >= fill.reference_price, "BUY 체결가가 기준가보다 낮음(낙관 회귀)"
    else:
        assert fill.price <= fill.reference_price, "SELL 체결가가 기준가보다 높음(낙관 회귀)"
    assert fill.price > 0


_FEE_BPS = ["0", "1", "50"]
_FEE_QTYS = ["0.000001", "1", "1000"]


@pytest.mark.parametrize(
    "maker_bps,taker_bps,qty", list(product(_FEE_BPS, _FEE_BPS, _FEE_QTYS))
)
def test_gate_red_line_fee_never_negative_or_rebate(
    maker_bps: str, taker_bps: str, qty: str
) -> None:
    model = FeeModel(
        maker_bps=Decimal(maker_bps), taker_bps=Decimal(taker_bps), fee_currency=Currency.USDT
    )
    order = make_order(quantity=qty)
    fills = FillModel(Decimal("1"), Decimal("1"), 0.0, Decimal("10")).simulate(
        order, make_book(), Decimal("1000"), FixedRng([])
    )
    fee = model.fee(fills[0])
    assert fee.amount >= 0, "수수료가 음수(리베이트) — 낙관 회귀"


# ---- D3: 다중 인스턴스/리플레이 적대적 증명 --------------------------------


def test_multi_instance_concurrent_replay_is_deterministic_and_race_free() -> None:
    """서로 다른 FillModel/LatencyModel *인스턴스* 64개를 스레드풀에서 동시에
    돌려도(실제 스레드 동시성 — GIL 아래에서도 바이트코드 경계 인터리빙은
    발생한다) 각 인스턴스는 순차 실행과 정확히 같은 결과를 낸다. 숨은 공유
    가변 상태(클래스 변수·모듈 전역 캐시)가 있다면 이 테스트가 흔들린다."""
    n_instances = 64

    def case_for(i: int) -> tuple[FillModel, LatencyModel, list[float]]:
        spread = Decimal(i % 7)
        impact = Decimal((i * 3) % 5)
        fill = FillModel(spread, impact, 0.3, Decimal("20"))
        latency = LatencyModel(ack_ms_p50=10.0 + i, ack_ms_p99=100.0 + i, drop_response_prob=0.1)
        rng_values = [((i * 37 + k) % 100) / 100 for k in range(4)]
        return fill, latency, rng_values

    cases = [case_for(i) for i in range(n_instances)]

    def run(i: int) -> tuple[list[SimFill], LatencyOutcome]:
        fill, latency, rng_values = cases[i]
        order = make_order(OrderSide.BUY if i % 2 == 0 else OrderSide.SELL, quantity="10")
        fills = fill.simulate(order, make_book(), Decimal("1000"), FixedRng(rng_values[:2]))
        outcome = latency.sample(FixedRng(rng_values[2:]))
        return fills, outcome

    expected = [run(i) for i in range(n_instances)]

    with ThreadPoolExecutor(max_workers=16) as pool:
        concurrent_results = list(pool.map(run, range(n_instances)))

    msg = "동시 다중 인스턴스 실행이 순차 리플레이와 다름(경쟁 상태 의심)"
    assert concurrent_results == expected, msg


async def test_latency_model_concurrent_tasks_share_instance_without_cross_talk() -> None:
    """D3 — 하나의 LatencyModel 인스턴스를 40개 동시 asyncio 태스크가 공유해도
    (강제 인터리빙 sleeper로 컨텍스트 스위치를 유발) 서로의 rng/상태를
    오염시키지 않는다 — 진짜 협조적 동시성 아래에서의 적대적 증명."""
    model = LatencyModel(ack_ms_p50=20.0, ack_ms_p99=200.0, drop_response_prob=0.2)
    n_tasks = 40

    async def chaos_sleeper(_seconds: float) -> None:
        await asyncio.sleep(0)  # 강제 컨텍스트 스위치 — 인터리빙 유발

    def rng_values(i: int) -> list[float]:
        return [((i * 13) % 100) / 100, ((i * 29) % 100) / 100]

    async def run(i: int) -> tuple[int, LatencyOutcome]:
        outcome = await model.apply(FixedRng(rng_values(i)), sleeper=chaos_sleeper)
        return i, outcome

    results = await asyncio.gather(*(run(i) for i in range(n_tasks)))

    for i, outcome in results:
        expected = model.sample(FixedRng(rng_values(i)))
        assert outcome == expected, f"task {i}: 동시 실행 결과가 순차 계산과 다름(상태 오염 의심)"
