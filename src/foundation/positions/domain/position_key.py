"""LB-2/FA-0d — position identifier (`position_key`) serialization/parsing,
the sole construction path.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-2
(`domain/position_key.py`: "position identifier `venue:instrument_id:
strategy_id:execution_id` serialization/parsing") +
docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-0d (§9 table row
113, "incorporate portfolio_id + central constructor").

The PK of `pos_journal`/`pos_snapshot` (`position_key VARCHAR(200)`, §9
LB-8) is stored as a raw string, but domain code reads and writes its
components only through this value object — this keeps string formatting
from scattering across the whole journal. A pure value object only — no I/O.

FA-0d incorporates `portfolio_id` as the 5th component — after the
multi-entity/multi-portfolio transition (FA-0b~FA-6), the same
`venue:instrument_id:strategy_id:execution_id` combination can be open
concurrently in different portfolios (e.g. multiple portfolios replicating
the same strategy), so 4 parts alone can no longer uniquely identify a
position. `scripts/check_position_key_central.py` statically finds code
outside this module that assembles a `position_key` string directly via
f-string/concat — this class (and `.parse()`) is the only legitimate
construction path.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

_DELIMITER = ":"
_STR_FIELDS = ("venue", "instrument_id", "strategy_id", "execution_id")
_FIELD_COUNT = len(_STR_FIELDS) + 1  # + portfolio_id


class InvalidPositionKeyError(ValueError):
    """The `position_key` string is not in the 5-part
    `venue:instrument_id:strategy_id:execution_id:portfolio_id` format, or a
    string component is empty or contains the delimiter (':'), or
    `portfolio_id` is not a valid UUID."""


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
