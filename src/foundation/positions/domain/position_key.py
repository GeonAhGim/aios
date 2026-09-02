"""LB-2 — 포지션 식별자(`position_key`) 직렬화·파싱.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-2
(`domain/position_key.py`: "포지션 식별자 `venue:instrument_id:strategy_id:
execution_id` 직렬화·파싱").

`pos_journal`/`pos_snapshot`의 PK(`position_key VARCHAR(200)`, §9 LB-8)는
원시 문자열로 저장되지만, 도메인 코드는 이 값 객체를 통해서만 구성요소를
읽고 쓴다 — 문자열 포매팅이 저널 전체에 흩어지는 것을 막는다. 순수
값 객체만 — I/O 없음.
"""
from __future__ import annotations

from dataclasses import astuple, dataclass

_DELIMITER = ":"
_FIELD_COUNT = 4


class InvalidPositionKeyError(ValueError):
    """`position_key` 문자열이 `venue:instrument_id:strategy_id:execution_id`
    4부분 형식이 아니거나, 구성요소가 비어 있거나 구분자(':')를 포함함."""


@dataclass(frozen=True, slots=True)
class PositionKey:
    venue: str
    instrument_id: str
    strategy_id: str
    execution_id: str

    def __post_init__(self) -> None:
        for name, value in zip(
            ("venue", "instrument_id", "strategy_id", "execution_id"), astuple(self), strict=True
        ):
            if not value:
                raise InvalidPositionKeyError(f"{name}는 비어 있을 수 없습니다.")
            if _DELIMITER in value:
                raise InvalidPositionKeyError(
                    f"{name}에 구분자({_DELIMITER!r})를 포함할 수 없습니다: {value!r}"
                )

    def __str__(self) -> str:
        return _DELIMITER.join(astuple(self))

    @classmethod
    def parse(cls, raw: str) -> PositionKey:
        parts = raw.split(_DELIMITER)
        if len(parts) != _FIELD_COUNT:
            raise InvalidPositionKeyError(
                f"position_key는 {_FIELD_COUNT}부분(venue:instrument_id:strategy_id:"
                f"execution_id)이어야 합니다: {raw!r}"
            )
        return cls(*parts)
