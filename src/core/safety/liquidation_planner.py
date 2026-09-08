"""R-50 / §9 R-50 — split liquidation planning (pure, seed-deterministic).

Spec: docs/specs/L4_risk_and_safety_v1.0.md#3.7, #9 R-50 (depends on R-21,
task-1194, `LiquidationPolicy`). Policy document 8.6-A-1.

Produces a `LiquidationPlan` only — no DB, clock, exchange, or unseeded
global `random` calls. All jitter/spacing is derived deterministically from
`seed` via SHA-256 counter-mode expansion: same inputs -> same plan, and a
1-byte change to `seed` changes every derived value. Execution (R-52
`liquidation_executor`) and request persistence (R-51) are out of scope.

`request_id` is not a `plan_liquidation` parameter (fixed signature). The
real id lives upstream, where `seed = HMAC(secret, request_id)` is computed
(§4.3 `liquidation_request` state table) — one-way, unrecoverable here. So
`LiquidationPlan.request_id` is derived from `seed` alone; the persistence
caller (R-51, out of scope) should re-tag it with the real row id.
"""
from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal
from typing import Literal

from pydantic import BaseModel, Field

from src.core.loader.risk_policy_loader import LiquidationPolicy

_HUNDRED = Decimal("100")
_TWO = Decimal("2")
_TWO_POW_64 = Decimal(2**64)


class OpenPosition(BaseModel, frozen=True):
    symbol: str
    quantity: Decimal = Field(gt=0)
    notional: Decimal = Field(gt=0)
    lot_size: Decimal = Field(gt=0)


class LiquidationSlice(BaseModel, frozen=True):
    seq: int
    symbol: str
    quantity: Decimal
    not_before_offset_sec: int
    order_type: Literal["LIMIT", "MARKET"]
    limit_tolerance_bps: int


class LiquidationPlan(BaseModel, frozen=True):
    request_id: uuid.UUID
    seed_hash: str
    slices: tuple[LiquidationSlice, ...]
    deadline_offset_sec: int
    adverse_move_abort_pct: Decimal


def _uniform_units(seed: bytes, tag: bytes, count: int) -> list[Decimal]:
    """`count` deterministic values in [0, 1) via SHA-256 counter mode on
    `seed || tag || counter`; distinct tags keep draw streams independent."""
    out: list[Decimal] = []
    for i in range(count):
        digest = hashlib.sha256(seed + b"|" + tag + b"|" + i.to_bytes(8, "big")).digest()
        out.append(Decimal(int.from_bytes(digest[:8], "big")) / _TWO_POW_64)
    return out


def _quantize_down(value: Decimal, lot: Decimal) -> Decimal:
    if value <= 0:
        return Decimal(0)
    units = (value / lot).to_integral_value(rounding=ROUND_DOWN)
    return units * lot


def _participation_cap(policy: LiquidationPolicy, volume_5m: Decimal | None) -> Decimal | None:
    if volume_5m is None:
        return None
    return (Decimal(str(policy.max_participation_pct)) / _HUNDRED) * volume_5m


def _slice_count(position: OpenPosition, policy: LiquidationPolicy, cap: Decimal | None) -> int:
    max_slice_notional = Decimal(str(policy.max_slice_notional))
    by_notional = int(
        (position.notional / max_slice_notional).to_integral_value(rounding=ROUND_CEILING)
    )
    n = max(policy.slice_count_min, min(by_notional, policy.slice_count_max))
    if cap is None:
        # Unknown participation is not "unlimited" — fail-closed to the
        # smallest possible slices (max slice count) instead of assuming a
        # large, unconstrained one.
        n = policy.slice_count_max
    else:
        while n < policy.slice_count_max and cap > 0 and (position.quantity / n) > cap:
            n += 1
    lots_available = int((position.quantity / position.lot_size).to_integral_value(ROUND_DOWN))
    return max(1, min(n, lots_available)) if lots_available > 0 else 1


def _slice_sizes(
    position: OpenPosition, policy: LiquidationPolicy, n: int, seed: bytes, cap: Decimal | None
) -> list[Decimal]:
    """Split quantity into `n` lot-quantized slices summing exactly to the
    original — same residual-absorption technique as
    `foundation/ledger/domain/rounding.split_commission` (round the leading
    parts, let the trailing part carry the remainder). LIMIT slices are also
    clamped to `cap` so jitter cannot exceed the participation limit."""
    lot = position.lot_size
    q = position.quantity
    base = q / n
    jitter_pct = Decimal(str(policy.size_jitter_pct)) / _HUNDRED
    cap_lot = _quantize_down(cap, lot) if cap is not None else None
    draws = _uniform_units(seed, b"jitter", n - 1)
    sizes: list[Decimal] = []
    remaining = q
    for i, u in enumerate(draws):
        jitter = (u * _TWO - 1) * jitter_pct
        raw = base * (1 + jitter)
        size = _quantize_down(raw, lot)
        if cap_lot is not None:
            size = min(size, cap_lot)
        reserve = lot * (n - 1 - i)  # keep at least one lot for each slice left, incl. MARKET
        max_allowed = remaining - reserve
        size = max(lot, min(size, max_allowed)) if max_allowed >= lot else lot
        sizes.append(size)
        remaining -= size
    sizes.append(remaining)  # last (MARKET) slice absorbs the rounding residual
    return sizes


def _not_before_offsets(policy: LiquidationPolicy, n: int, seed: bytes) -> list[int]:
    lo = Decimal(str(policy.interval_min_sec))
    hi = Decimal(str(policy.interval_max_sec))
    span = hi - lo
    draws = _uniform_units(seed, b"interval", n)
    offsets: list[int] = []
    cumulative = Decimal(0)
    for u in draws:
        cumulative += lo + u * span
        offsets.append(int(cumulative.to_integral_value(rounding=ROUND_DOWN)))
    return offsets


def _plan_position(
    position: OpenPosition,
    seed: bytes,
    policy: LiquidationPolicy,
    volume_5m: Decimal | None,
    start_seq: int,
) -> list[LiquidationSlice]:
    # `seed` is already scoped to this position's symbol (see plan_liquidation),
    # so every draw below is independent per symbol.
    cap = _participation_cap(policy, volume_5m)
    n = _slice_count(position, policy, cap)
    sizes = _slice_sizes(position, policy, n, seed, cap)
    offsets = _not_before_offsets(policy, n, seed)
    tolerance_bps = int(round(policy.limit_tolerance_bps))
    return [
        LiquidationSlice(
            seq=start_seq + i,
            symbol=position.symbol,
            quantity=sizes[i],
            not_before_offset_sec=offsets[i],
            order_type="MARKET" if i == n - 1 else "LIMIT",
            limit_tolerance_bps=tolerance_bps,
        )
        for i in range(n)
    ]


def plan_liquidation(
    positions: Sequence[OpenPosition],
    *,
    seed: bytes,
    policy: LiquidationPolicy,
    volume_5m: Mapping[str, Decimal | None],
) -> LiquidationPlan:
    """Deterministic, sum-preserving split-liquidation plan: same inputs
    always produce the same `LiquidationPlan`; a 1-byte `seed` change
    changes every slice's jitter and schedule."""
    request_id = uuid.UUID(bytes=hashlib.sha256(b"request_id|" + seed).digest()[:16])
    seed_hash = hashlib.sha256(seed).hexdigest()
    slices: list[LiquidationSlice] = []
    seq = 0
    for position in positions:
        position_seed = seed + b"|" + position.symbol.encode()
        position_slices = _plan_position(
            position, position_seed, policy, volume_5m.get(position.symbol), seq
        )
        slices.extend(position_slices)
        seq += len(position_slices)
    return LiquidationPlan(
        request_id=request_id,
        seed_hash=seed_hash,
        slices=tuple(slices),
        deadline_offset_sec=int(policy.total_deadline_sec),
        adverse_move_abort_pct=Decimal(str(policy.adverse_move_abort_pct)),
    )
