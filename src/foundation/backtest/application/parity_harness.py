"""BT-19 — 백테스트=라이브 패리티 하네스(I-05 강제).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.5
BT-19, ADR-2026-09-06-G §8, docs/design/INVARIANTS.md I-05("백테스트와
라이브는 같은 컴파일 산출물·같은 도메인 로직을 공유").

DSL-11(신호엔진 신구 비교)과 BT-9(같은 시드 재현성)는 "PAPER에서 실제로
일어난 체결"과 "백테스트가 같은 아티팩트·구간을 재생해 낸 체결"을 대조하지
않는다 — I-05를 실제로 검증하는 리프가 없었다. 이 모듈이 그 대조를
맡는다: 호출자가 이미 확보한 두 시퀀스(PAPER 실행 추적의 `FillEvent`들,
`run_backtest`가 같은 아티팩트·구간을 재생해 낸 `SimulatedFill`들)를
받아, 타임스탬프를 제외한 필드로 순서대로 항목별 대조하고 첫 발산
지점을 보고한다.

순수 비교 로직 — I/O 없음. PAPER 추적을 어디서 가져오는지(`fills` 테이블
조회 등, `src/services/oms/adapters/fills_repository.py`)와 재생 자체를
어떻게 실행하는지(`run_backtest`)는 이 모듈의 책임이 아니다.

값은 `Decimal` 동등성으로 비교한다(같은 값이면 지수 표현이 달라도
동일로 본다) — 비교 대상 필드(symbol/side/quantity/price/fee) 자체가
이미 정형 타입이라 원본 바이트 표현의 차이는 의미가 없다. 타임스탬프
(PAPER의 `venue_ts`, 백테스트의 `timestamp`)만 비교에서 제외한다.

Fail-closed: 길이가 다르면 더 짧은 쪽 길이를 발산 지점으로 즉시 보고한다
(꼬리 쪽을 조용히 무시하지 않는다).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from src.data.models.trading import OrderSide
from src.foundation.backtest.domain.models import SimulatedFill
from src.services.oms.contracts.v1_events import FillEvent

_COMPARED_FIELDS: Final = ("symbol", "side", "quantity", "price", "fee")
_LENGTH_MISMATCH_FIELD: Final = "__length__"


@dataclass(frozen=True)
class ComparableFill:
    """패리티 비교에 쓰는 정준 체결 표현 — 타임스탬프를 제외한다."""

    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    fee: Decimal


def paper_fill_to_comparable(fill: FillEvent) -> ComparableFill:
    """PAPER 실행 추적의 `FillEvent` 1건 → 비교용 정준 표현."""
    return ComparableFill(
        symbol=fill.symbol,
        side=fill.side,
        quantity=fill.quantity,
        price=fill.price,
        fee=fill.fee,
    )


def backtest_fill_to_comparable(fill: SimulatedFill) -> ComparableFill:
    """백테스트 리플레이의 `SimulatedFill` 1건 → 비교용 정준 표현."""
    return ComparableFill(
        symbol=fill.symbol,
        side=fill.side,
        quantity=fill.quantity,
        price=fill.price,
        fee=fill.fee,
    )


@dataclass(frozen=True)
class Divergence:
    """첫 발산 지점 — 리포트에 반드시 담아 어디서부터 어긋났는지 밝힌다."""

    index: int
    field: str
    paper_value: str
    backtest_value: str


@dataclass(frozen=True)
class ParityReport:
    is_match: bool
    paper_fill_count: int
    backtest_fill_count: int
    first_divergence: Divergence | None

    def raise_if_mismatch(self) -> None:
        """하네스를 게이트로 쓸 때(CI 등) — 불일치면 즉시 예외로 실패시킨다."""
        if not self.is_match:
            raise ParityMismatchError(self)


class ParityMismatchError(AssertionError):
    def __init__(self, report: ParityReport) -> None:
        self.report = report
        super().__init__(_format_mismatch(report))


def _format_mismatch(report: ParityReport) -> str:
    d = report.first_divergence
    if d is None:  # pragma: no cover — is_match=False면 항상 first_divergence가 있다
        return "parity mismatch: 발산 지점 없이 불일치로 보고됨(호출자 오류)"
    if d.field == _LENGTH_MISMATCH_FIELD:
        return (
            f"parity mismatch at index {d.index}: 체결 개수 불일치 "
            f"(paper={report.paper_fill_count}건, backtest={report.backtest_fill_count}건)"
        )
    return (
        f"parity mismatch at index {d.index}: field={d.field!r} "
        f"paper={d.paper_value!r} backtest={d.backtest_value!r}"
    )


def check_parity(
    paper_trace: Sequence[FillEvent],
    backtest_fills: Sequence[SimulatedFill],
) -> ParityReport:
    """PAPER 실행 추적과 백테스트 리플레이 체결을 순서대로 항목별 대조한다.

    두 시퀀스는 같은 아티팩트·같은 구간을 대상으로 한 것이라고 가정한다
    (그 정렬 자체는 호출자 책임 — 이 함수는 순서를 신뢰하고 인덱스로만
    비교한다). 공통 길이 구간에서 필드 하나라도 다르면 그 인덱스가 첫
    발산 지점이고, 공통 구간이 전부 같은데 길이가 다르면 더 짧은 쪽
    길이가 발산 지점이다.
    """
    paper = [paper_fill_to_comparable(f) for f in paper_trace]
    backtest = [backtest_fill_to_comparable(f) for f in backtest_fills]

    common_len = min(len(paper), len(backtest))
    for index in range(common_len):
        divergence = _first_field_divergence(index, paper[index], backtest[index])
        if divergence is not None:
            return ParityReport(
                is_match=False,
                paper_fill_count=len(paper),
                backtest_fill_count=len(backtest),
                first_divergence=divergence,
            )

    if len(paper) != len(backtest):
        return ParityReport(
            is_match=False,
            paper_fill_count=len(paper),
            backtest_fill_count=len(backtest),
            first_divergence=Divergence(
                index=common_len,
                field=_LENGTH_MISMATCH_FIELD,
                paper_value=str(len(paper)),
                backtest_value=str(len(backtest)),
            ),
        )

    return ParityReport(
        is_match=True,
        paper_fill_count=len(paper),
        backtest_fill_count=len(backtest),
        first_divergence=None,
    )


def _first_field_divergence(
    index: int, paper: ComparableFill, backtest: ComparableFill
) -> Divergence | None:
    for field in _COMPARED_FIELDS:
        paper_value = getattr(paper, field)
        backtest_value = getattr(backtest, field)
        if paper_value != backtest_value:
            return Divergence(
                index=index,
                field=field,
                paper_value=str(paper_value),
                backtest_value=str(backtest_value),
            )
    return None


__all__ = [
    "ComparableFill",
    "Divergence",
    "ParityMismatchError",
    "ParityReport",
    "backtest_fill_to_comparable",
    "check_parity",
    "paper_fill_to_comparable",
]
