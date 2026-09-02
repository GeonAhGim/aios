"""Strategy Validation 순수 규칙 함수 — DB/HTTP 없이 단위 테스트 가능해야 한다.

Spec: AIOSproject 76번 §3(validation policy)/§6(STR-001 재현성).
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from src.data.models.market_data import Candle
from src.foundation.validation.domain.models import Outcome


def _bar_fingerprint(bar: Candle) -> str:
    return "|".join(
        [
            bar.symbol,
            bar.exchange,
            bar.timeframe,
            bar.open_time.isoformat(),
            str(bar.open),
            str(bar.high),
            str(bar.low),
            str(bar.close),
            str(bar.volume),
        ]
    )


def compute_input_snapshot_hash(
    *,
    fsm_definition: dict[str, Any],
    cost_model: dict[str, Any],
    warmup_bars: int,
    periods_per_year: int,
    initial_equity: Decimal,
    bars: list[Candle],
) -> str:
    """76번 §1 "input/config pinned before queue", STR-001 "same source/
    config/input/seed compiles and validates to same ... result hash". FSM
    정의·비용모델·bar 시퀀스 중 하나라도 달라지면 다른 해시가 나와야
    한다 — 그래야 같은 전략을 다시 검증했을 때 캐시 재사용(멱등성)과
    "진짜로 달라졌으니 재검증 필요"를 구분할 수 있다."""
    payload = json.dumps(
        {
            "fsm_definition": fsm_definition,
            "cost_model": cost_model,
            "warmup_bars": warmup_bars,
            "periods_per_year": periods_per_year,
            "initial_equity": str(initial_equity),
            "bars": [_bar_fingerprint(b) for b in bars],
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_result_hash(metrics: dict[str, Any]) -> str:
    payload = json.dumps(metrics, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evaluate_validation_policy(warnings: list[str]) -> tuple[Outcome, list[str]]:
    """76번 §3 "only explicit obligations may be carried to package state" —
    엔진(FND-10)이 낸 경고를 조용히 버리지 않고 전부 obligation으로
    승격한다. 이 체크(check_type='backtest')는 하드페일 조건이 없다 —
    `run_backtest()` 자체가 실패하면(warmup 부족 등) 그건 정책 판정 이전에
    실행 자체가 실패한 것이라 이 함수에 도달하지 않는다(application 계층이
    별도 FAILED로 처리)."""
    if warnings:
        return Outcome.PASS_WITH_OBLIGATIONS, list(warnings)
    return Outcome.PASS, []
