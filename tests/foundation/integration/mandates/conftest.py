from __future__ import annotations

from uuid import UUID

import asyncpg

from src.foundation.mandates.contracts.v1 import Autonomy, MandateRuleInput


def default_rules(**overrides: object) -> MandateRuleInput:
    defaults: dict[str, object] = dict(
        max_total_exposure_pct=80.0,
        max_single_instrument_pct=20.0,
        min_cash_buffer_pct=5.0,
        max_daily_loss_pct=3.0,
        allowed_autonomy=Autonomy.PAPER,
        forbidden_assets=["XYZ"],
    )
    defaults.update(overrides)
    return MandateRuleInput(**defaults)  # type: ignore[arg-type]


async def backdate_cooling_off(pool: asyncpg.Pool, revision_id: UUID, seconds_ago: int) -> None:
    """activate_revision.py의 cooling-off 검사는 injectable clock이 없다 —
    MFA 테스트가 MutableClock을 쓴 것과 달리, 이 리프는 시간 자체를 DB에서
    과거로 되돌리는 방식으로 재현한다(둘 다 결정론적으로 "구간 경과"를
    재현하는 게 목적, 수단만 다름)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE mandate_revision "
            "SET cooling_off_started_at = now() - ($2 * interval '1 second') "
            "WHERE id = $1",
            revision_id,
            seconds_ago,
        )
