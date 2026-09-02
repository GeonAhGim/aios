"""LA-6 — 로그수익률 rolling median/MAD 스파이크 + 인접 캔들 high/low 비율 상한.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-6, §9.2 LA-6.

순수 함수 — I/O·전역 시계 없음. `candles`는 이미 OHLC sanity(LA-4)를 통과해
가격이 양수라고 가정한다(그 전제가 깨지면 `Decimal.ln()`이
`InvalidOperation`을 낸다 — sanity/dedupe는 이 리프의 소관이 아니다).
임계값(`k_mad` 기본값, MAD 하한, high/low 비율 상한)은 스펙이 "Draft"로
표시한 값이라 실거래소 데이터로 검증되지 않았다(미검증).
"""
from __future__ import annotations

from decimal import Decimal

from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    QualityIssue,
    QualityIssueType,
    Severity,
)

__all__ = ["detect_spikes"]

_MIN_WINDOW = 10
_MAD_FLOOR = Decimal("0.0001")
_HL_RATIO_CAP = Decimal("3")


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _log_returns(candles: list[CandleRecord]) -> list[Decimal]:
    return [(cur.close / prev.close).ln() for prev, cur in zip(candles, candles[1:], strict=False)]


def _spike_issue(candle: CandleRecord, reason: str) -> QualityIssue:
    return QualityIssue(
        type=QualityIssueType.SPIKE,
        severity=Severity.WARN,
        open_time=candle.open_time,
        detail={"reason": reason},
    )


def _hl_ratio_exceeded(prev: CandleRecord, cur: CandleRecord) -> bool:
    """직전 캔들 대비 high 급등 또는 low 급락(둘 다 `_HL_RATIO_CAP`배 상한)."""
    if prev.high > 0 and cur.high > prev.high * _HL_RATIO_CAP:
        return True
    return bool(cur.low > 0 and prev.low > cur.low * _HL_RATIO_CAP)


def detect_spikes(
    candles: list[CandleRecord], window: int = 60, k_mad: Decimal = Decimal("8")
) -> list[QualityIssue]:
    """`§4.1` SPIKE(WARN) 탐지 — 두 독립 채널 중 하나라도 걸리면 캔들당
    이슈 1개만 낸다(중복 방지). 채널 1: 로그수익률이 트레일링(현재 캔들
    제외) median 대비 `k_mad·MAD`를 초과. 채널 2: high/low가 직전 캔들
    대비 `_HL_RATIO_CAP`배를 초과(종가는 정상이어도 고가/저가만 튀는
    경우를 잡는다). 트레일링 표본이 `_MIN_WINDOW` 미만이면 채널 1은
    건너뛴다(오탐 방지 — 판단할 데이터가 부족하면 판정하지 않는다)."""
    issues: list[QualityIssue] = []
    if len(candles) < 2:
        return issues
    log_returns = _log_returns(candles)
    for idx in range(1, len(candles)):
        cur, prev = candles[idx], candles[idx - 1]
        r = log_returns[idx - 1]
        trailing = log_returns[max(0, idx - 1 - window) : idx - 1]
        if len(trailing) >= _MIN_WINDOW:
            median = _median(trailing)
            mad = max(_median([abs(x - median) for x in trailing]), _MAD_FLOOR)
            if abs(r - median) > k_mad * mad:
                issues.append(_spike_issue(cur, "log_return_mad"))
                continue
        if _hl_ratio_exceeded(prev, cur):
            issues.append(_spike_issue(cur, "hl_ratio"))
    return issues
