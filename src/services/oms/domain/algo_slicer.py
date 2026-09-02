"""TWAP/VWAP/POV/iceberg 슬라이스 계획(순수)(L4 명세 §2-A, R13).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-A, §9(L4-25 도메인
부분만 — `application/algo_executor.py`는 별도 리프, 이 파일은 계획기만).

R13 — "실행 알고리즘" — 참여율 상한(anti-front-running), 슬라이스
무작위화(크기·시간 지터). Phase 1은 TWAP만 실제 실행(06번 §6.1), 이
계획기 자체는 알고리즘 종류와 무관하게 순수 함수다 — 실행 활성화 여부는
`application/algo_executor.py`(다른 리프)의 책임.

`rng: random.Random`을 주입받는다(watchdog.py/MfaService와 동일 원칙) —
같은 `req.seed`로 만든 `random.Random(req.seed)`를 넘기면 재현 가능한
무작위화가 된다(§3.1 `AlgoRequest.seed` "재현 가능한 무작위화").
"""
from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from src.services.oms.contracts.v1_commands import AlgoRequest
from src.services.oms.domain.rounding import round_qty

_DEFAULT_QTY_GRANULARITY = Decimal("0.00000001")
"""슬라이스 계획 단계는 실제 venue의 lot 크기를 모른다(symbol/venue가
계획 시점에 아직 확정 안 될 수 있음, §2-A 의존 목록도 `rounding`만이지
`symbol_registry`가 아니다) — 흔한 크립토 정밀도로 임시 양자화만 하고,
실제 lot 라운딩은 각 슬라이스가 실제 주문으로 나갈 때(algo_executor →
submit_order) venue별로 다시 한다."""


@dataclass(frozen=True)
class SlicePlan:
    sequence: int
    scheduled_at: datetime
    quantity: Decimal


def plan_slices(
    req: AlgoRequest,
    *,
    now: datetime,
    volume_profile: Sequence[Decimal] | None,
    rng: random.Random,
) -> list[SlicePlan]:
    if req.end_at <= req.start_at:
        raise ValueError("end_at은 start_at보다 뒤여야 합니다.")

    total_span_sec = (req.end_at - req.start_at).total_seconds()
    base_interval_sec = total_span_sec / req.slice_count
    base_qty = req.total_quantity / req.slice_count

    size_jitter_fraction = req.size_jitter_pct / Decimal(100)
    time_jitter_fraction = req.time_jitter_pct / Decimal(100)
    max_participation_fraction = req.max_participation_pct / Decimal(100)

    plans: list[SlicePlan] = []
    allocated = Decimal("0")

    for sequence in range(req.slice_count):
        is_last = sequence == req.slice_count - 1
        remaining = req.total_quantity - allocated

        nominal_offset_sec = base_interval_sec * sequence
        time_jitter_sec = base_interval_sec * float(time_jitter_fraction) * (
            rng.uniform(-1.0, 1.0)
        )
        scheduled_at = req.start_at + timedelta(seconds=nominal_offset_sec + time_jitter_sec)
        scheduled_at = max(req.start_at, min(req.end_at, scheduled_at))

        if is_last:
            # 잔량 전부 흡수 — Σ(quantity) == req.total_quantity를 정확히
            # 보장한다(부동소수 없이 Decimal 뺄셈이라 오차 없음).
            quantity = remaining
        else:
            size_jitter_multiplier = Decimal(1) + size_jitter_fraction * Decimal(
                str(rng.uniform(-1.0, 1.0))
            )
            quantity = base_qty * size_jitter_multiplier

            if volume_profile is not None and sequence < len(volume_profile):
                participation_cap = volume_profile[sequence] * max_participation_fraction
                quantity = min(quantity, participation_cap)

            # 앞선 슬라이스들이 지터로 과할당돼 마지막 슬라이스가 음수가
            # 되는 것을 막는다 — 매 슬라이스가 "남은 잔량"을 넘지 않는다.
            quantity = min(quantity, remaining)
            quantity = max(Decimal("0"), round_qty(quantity, _DEFAULT_QTY_GRANULARITY))

        allocated += quantity
        plans.append(SlicePlan(sequence=sequence, scheduled_at=scheduled_at, quantity=quantity))

    return plans
