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


_HARD_FAIL_MARKERS: tuple[str, ...] = (
    # 76번 §3 R5/point-in-time — 재생 도중 FD-8.1/8.2 상태 불일치(로직
    # 버그)가 실제로 발생한 경우. run_backtest()가 예외를 삼키고 경고로
    # 남기므로(BacktestRunError처럼 즉시 죽지 않음) 여기서 반드시 잡는다.
    "PortfolioEngine 예외",
    # 76번 §3 robustness 행 — L34(overfitting.py)가 계산을 시작하면 이
    # 문구를 포함한 경고를 내도록 만든다(ofit-v1). 아직 계산기가 없어
    # 지금은 도달하지 않지만, 규칙표를 미리 열어둬 나중에 배선만 하면
    # 된다(107번 계약 안정성 원칙과 동일하게 "조용한 우회"를 막는다).
    "DSR",
    "PBO",
    # 76번 §3 point-in-time 행 — look-ahead/survivorship 데이터 누수.
    "LOOKAHEAD",
    "데이터 누수",
)


def _is_hard_fail(warning: str) -> bool:
    upper = warning.upper()
    return any(marker.upper() in upper for marker in _HARD_FAIL_MARKERS)


def evaluate_validation_policy(
    warnings: list[str],
) -> tuple[Outcome, list[str], list[str]]:
    """76번 §3 "only explicit obligations may be carried to package state" —
    엔진(FND-10)이 낸 경고 중 `_HARD_FAIL_MARKERS`에 해당하지 않는 것만
    obligation으로 승격하고, 나머지(백테스트 오류·DSR/PBO 임계 미달·데이터
    누수 등)는 hard_fail_reasons로 분류해 FAIL을 반환한다 — I-07 "검증/
    승인 게이트의 hard-fail 조건은 도메인 코드가 계산하고 실제로 FAIL을
    반환할 수 있어야 한다"의 최소 구현. `run_backtest()` 자체가 실패하면
    (warmup 부족 등) 그건 정책 판정 이전에 실행 자체가 실패한 것이라 이
    함수에 도달하지 않는다(application 계층이 별도 FAILED로 처리)."""
    hard_fail_reasons = [w for w in warnings if _is_hard_fail(w)]
    obligations = [w for w in warnings if not _is_hard_fail(w)]
    if hard_fail_reasons:
        return Outcome.FAIL, obligations, hard_fail_reasons
    if obligations:
        return Outcome.PASS_WITH_OBLIGATIONS, obligations, []
    return Outcome.PASS, [], []
