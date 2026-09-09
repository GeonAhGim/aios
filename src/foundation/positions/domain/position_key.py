"""LB-2/FA-0d — 포지션 식별자(`position_key`) 직렬화·파싱, 유일한 생성 경로.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-2
(`domain/position_key.py`: "포지션 식별자 `venue:instrument_id:strategy_id:
execution_id` 직렬화·파싱") +
docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-0d (§9 표 113행,
"portfolio_id 편입 + 중앙 생성자").

`pos_journal`/`pos_snapshot`의 PK(`position_key VARCHAR(200)`, §9 LB-8)는
원시 문자열로 저장되지만, 도메인 코드는 이 값 객체를 통해서만 구성요소를
읽고 쓴다 — 문자열 포매팅이 저널 전체에 흩어지는 것을 막는다. 순수
값 객체만 — I/O 없음.

FA-0d에서 `portfolio_id`를 5번째 구성요소로 편입한다 — 다법인/다포트폴리오
전환(FA-0b~FA-6) 이후 같은 `venue:instrument_id:strategy_id:execution_id`
조합이 서로 다른 포트폴리오에서 동시에 열릴 수 있어(예: 같은 전략을 여러
포트폴리오가 복제), 4부분만으로는 더 이상 포지션을 유일하게 식별하지
못한다. `scripts/check_position_key_central.py`가 이 모듈 밖에서
`position_key` 문자열을 f-string/concat으로 직접 조립하는 코드를 정적으로
찾아낸다 — 이 클래스(와 `.parse()`)가 유일한 합법적 생성 경로다.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

_DELIMITER = ":"
_STR_FIELDS = ("venue", "instrument_id", "strategy_id", "execution_id")
_FIELD_COUNT = len(_STR_FIELDS) + 1  # + portfolio_id


class InvalidPositionKeyError(ValueError):
    """`position_key` 문자열이 `venue:instrument_id:strategy_id:execution_id:
    portfolio_id` 5부분 형식이 아니거나, 문자열 구성요소가 비어 있거나
    구분자(':')를 포함하거나, `portfolio_id`가 유효한 UUID가 아님."""


@dataclass(frozen=True, slots=True)
class PositionKey:
    venue: str
    instrument_id: str
    strategy_id: str
    execution_id: str
    portfolio_id: UUID

    def __post_init__(self) -> None:
        for name in _STR_FIELDS:
            value = getattr(self, name)
            if not value:
                raise InvalidPositionKeyError(f"{name}는 비어 있을 수 없습니다.")
            if _DELIMITER in value:
                raise InvalidPositionKeyError(
                    f"{name}에 구분자({_DELIMITER!r})를 포함할 수 없습니다: {value!r}"
                )
        if not isinstance(self.portfolio_id, UUID):
            raise InvalidPositionKeyError(
                f"portfolio_id는 UUID여야 합니다: {self.portfolio_id!r}"
            )

    def __str__(self) -> str:
        return _DELIMITER.join(
            (self.venue, self.instrument_id, self.strategy_id, self.execution_id,
             str(self.portfolio_id))
        )

    @classmethod
    def parse(cls, raw: str) -> PositionKey:
        parts = raw.split(_DELIMITER)
        if len(parts) != _FIELD_COUNT:
            raise InvalidPositionKeyError(
                f"position_key는 {_FIELD_COUNT}부분(venue:instrument_id:strategy_id:"
                f"execution_id:portfolio_id)이어야 합니다: {raw!r}"
            )
        venue, instrument_id, strategy_id, execution_id, portfolio_id_raw = parts
        try:
            portfolio_id = UUID(portfolio_id_raw)
        except ValueError as exc:
            raise InvalidPositionKeyError(
                f"portfolio_id는 UUID여야 합니다: {portfolio_id_raw!r}"
            ) from exc
        return cls(venue, instrument_id, strategy_id, execution_id, portfolio_id)
