"""EMS TCA API -- 71 §6: router only does auth/DI/transport validation/command
invocation.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §2/§9 EM-14.

Closes the gap review task-4794 REJECTed task-4013 for: `application/
compute_tca.py` (EM-14) existed with no adapter and no route, so a computed
TCA result had nowhere to go and no way to be read back. `:compute` assembles
and persists one revision (idempotent on `(parent_id, revision)`, see
`adapters/tca_storage.py`); `GET /{parent_id}` returns the latest revision.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import get_current_user
from src.api.foundation_deps import get_tca_result_repository
from src.api.schemas.foundation.ems import ComputeTcaRequest, TcaResultView
from src.data.models.market_data import Candle
from src.foundation.ems.application.compute_tca import compute_tca
from src.foundation.ems.domain.tca.benchmarks import Fill
from src.foundation.ems.ports.tca_result_repository import TcaResultRecord, TcaResultRepository
from src.services.auth_service import User

router = APIRouter(prefix="/v1/foundation/ems/tca", tags=["foundation:ems:tca"])


def _to_view(record: TcaResultRecord) -> TcaResultView:
    return TcaResultView(
        parent_id=record.parent_id,
        revision=record.revision,
        result=record.result,
        computed_at=record.computed_at,
    )


@router.post("/{parent_id}:compute", status_code=status.HTTP_202_ACCEPTED)
async def post_compute_tca(
    parent_id: UUID,
    body: ComputeTcaRequest,
    _user: User = Depends(get_current_user),
    repo: TcaResultRepository = Depends(get_tca_result_repository),
) -> ApiResponse[TcaResultView]:
    fills = [Fill(price=f.price, qty=f.qty) for f in body.fills]
    bars = [
        Candle(
            symbol="",
            exchange="",
            timeframe="",
            open=bar.close,
            high=bar.close,
            low=bar.close,
            close=bar.close,
            volume=bar.volume,
            open_time=body.computed_at,
            close_time=body.computed_at,
        )
        for bar in body.bars
    ]

    record = await compute_tca(
        repo,
        parent_id=parent_id,
        side=body.side,
        fills=fills,
        price_at_arrival_ts=body.price_at_arrival_ts,
        bars=bars,
        spread_cost=body.spread_cost,
        fees=body.fees,
        total_cost=body.total_cost,
        revision=body.revision,
        computed_at=body.computed_at,
    )
    return ok(_to_view(record))


@router.get("/{parent_id}")
async def get_latest_tca(
    parent_id: UUID,
    _user: User = Depends(get_current_user),
    repo: TcaResultRepository = Depends(get_tca_result_repository),
) -> ApiResponse[TcaResultView]:
    record = await repo.get_latest(parent_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "해당 parent_id의 TCA 결과가 없습니다.")
    return ok(_to_view(record))


@router.get("/{parent_id}/revisions/{revision}")
async def get_tca_revision(
    parent_id: UUID,
    revision: int,
    _user: User = Depends(get_current_user),
    repo: TcaResultRepository = Depends(get_tca_result_repository),
) -> ApiResponse[TcaResultView]:
    record = await repo.get_by_revision(parent_id, revision)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "해당 revision의 TCA 결과가 없습니다.")
    return ok(_to_view(record))
