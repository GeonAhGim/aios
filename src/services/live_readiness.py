"""모의 운용 -> 실전 전환 근거 계산 (J8, J8_LIVE_PROMOTION_PLAN_2026-11-06.md LP-1).

PAPER 실행 하나의 주문 행(`paper_sim_orders` 또는 `orders`와 같은 모양)에서 사용자가 전환을
판단할 근거를 계산하는 순수 함수다. DB를 모른다 -- 호출부가 행을 읽어 넘긴다.

원칙: **자료가 없는 항목은 0이 아니라 None + 사유(`unavailable`)로 명시한다.**
없는 데이터를 좋은 성과로 보이게 하지 않는다.

- 수수료: 행에 `fee_total`이 있을 때만(`paper_sim_orders`에는 있고 `orders`에는 없다).
  통화가 섞이면 통화별로 나눠 싣는다.
- 슬리피지: 가격과 평균 체결가가 둘 다 있고 체결된 주문만. 기준가가 없는 시장가는
  제외하고 표본 수를 함께 싣는다.
- 순손익·장애 복구 이벤트: 주문 행만으로는 알 수 없다 -- 항상 unavailable(원천 배선 전).
- UNKNOWN 상태는 실패로 단정하지 않는다(8.3 원칙) -- 비율 분모에서 빼고 따로 센다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

# 끝난 주문(비율 분모). 그 밖(CREATED..ACKNOWLEDGED, PARTIALLY_FILLED, CANCEL_REQUESTED)은
# 진행 중이고, UNKNOWN은 따로 센다.
_TERMINAL = frozenset({"FILLED", "CANCELLED", "EXPIRED", "REJECTED", "FAILED"})
_NOT_FILLED_TERMINAL = frozenset({"CANCELLED", "EXPIRED", "FAILED"})
_BPS = Decimal(10000)

UNAVAILABLE_NET_PNL = "주문 행만으로는 순손익을 알 수 없다(포지션·원장 원천 미배선)"
UNAVAILABLE_RECOVERY = "장애 복구 이벤트 원천 미배선"
UNAVAILABLE_NO_TERMINAL = "끝난 주문이 없어 비율을 계산할 수 없다"
UNAVAILABLE_NO_FEE_COLUMN = "주문 행에 수수료 열이 없다"
UNAVAILABLE_PARTIAL_FEE = "일부 주문에만 수수료가 있어 합계를 내면 과소 보고가 된다"
UNAVAILABLE_NO_SLIPPAGE_SAMPLE = "가격과 평균 체결가가 둘 다 있는 체결된 지정가 주문이 없다"


class LiveReadinessInputError(ValueError):
    """주문 행이 계산할 수 없는 모양일 때(수량 <= 0, 체결수량 > 수량, 알 수 없는 side 등)."""


@dataclass(frozen=True)
class LiveReadiness:
    order_count: int
    terminal_count: int
    open_count: int
    unknown_count: int
    filled_count: int
    partial_fill_count: int
    unfilled_count: int
    rejected_count: int
    fill_ratio: Decimal | None
    partial_fill_ratio: Decimal | None
    unfilled_ratio: Decimal | None
    reject_ratio: Decimal | None
    period_start: datetime | None
    period_end: datetime | None
    period_days: Decimal | None
    avg_slippage_bps: Decimal | None
    slippage_sample_size: int
    fee_totals: dict[str, Decimal] | None
    net_pnl: Decimal | None
    recovery_event_count: int | None
    # 값이 None인 항목 -> 이유. 화면은 이 사유를 그대로 보여 줘야 한다.
    unavailable: dict[str, str] = field(default_factory=dict)


def _dec(value: Any, name: str) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001 - 어떤 값이 왜 틀렸는지만 알리면 된다
        raise LiveReadinessInputError(f"{name}을(를) 숫자로 읽을 수 없다: {value!r}") from exc


def _ratio(count: int, total: int) -> Decimal | None:
    return None if total <= 0 else Decimal(count) / Decimal(total)


def _validate_order(row: Mapping[str, Any]) -> tuple[Decimal, Decimal, str]:
    """Validate quantities and side before accumulating any evidence."""
    quantity = _dec(row.get("quantity"), "quantity")
    done = _dec(row.get("filled_quantity"), "filled_quantity") or Decimal(0)
    if quantity is None or quantity <= 0:
        raise LiveReadinessInputError(f"quantity는 0보다 커야 한다: {row.get('quantity')!r}")
    if done < 0 or done > quantity:
        raise LiveReadinessInputError(f"filled_quantity가 범위를 벗어났다: {done} / {quantity}")
    side = str(row.get("side") or "").upper()
    if side not in ("BUY", "SELL"):
        raise LiveReadinessInputError(f"알 수 없는 side: {row.get('side')!r}")
    return quantity, done, side


@dataclass
class _OrderCounts:
    """Keep status classification independent of price and fee evidence."""

    unknown_count: int = 0
    open_count: int = 0
    terminal: int = 0
    filled: int = 0
    rejected: int = 0
    unfilled: int = 0
    partial: int = 0

    def add(self, status: str, quantity: Decimal, done: Decimal) -> None:
        if status == "UNKNOWN":
            self.unknown_count += 1
        elif status not in _TERMINAL:
            self.open_count += 1
        else:
            self.terminal += 1
            if status == "FILLED":
                self.filled += 1
            elif status == "REJECTED":
                self.rejected += 1
            elif status in _NOT_FILLED_TERMINAL and done == 0:
                self.unfilled += 1
        if 0 < done < quantity:
            self.partial += 1


def _slippage_sample(
    row: Mapping[str, Any], done: Decimal, side: str
) -> tuple[Decimal, Decimal, int]:
    """Return quantity-weighted adverse slippage and its sample weight."""
    price = _dec(row.get("price"), "price")
    avg = _dec(row.get("average_fill_price"), "average_fill_price")
    if done > 0 and price is not None and price > 0 and avg is not None:
        diff = (avg - price) if side == "BUY" else (price - avg)
        return diff / price * _BPS * done, done, 1
    return Decimal(0), Decimal(0), 0


def compute_live_readiness(rows: Iterable[Mapping[str, Any]]) -> LiveReadiness:
    """주문 행들에서 전환 근거를 계산한다. 행 키: status, side, quantity, filled_quantity, price,
    average_fill_price, created_at, updated_at, (선택) fee_total, fee_currency."""
    order_count = 0
    counts = _OrderCounts()
    starts: list[datetime] = []
    ends: list[datetime] = []
    fee_rows = 0
    fee_totals: dict[str, Decimal] = {}
    slip_weighted = Decimal(0)
    slip_qty = Decimal(0)
    slip_n = 0

    for row in rows:
        order_count += 1
        status = str(row.get("status") or "").upper()
        quantity, done, side = _validate_order(row)

        created, updated = row.get("created_at"), row.get("updated_at")
        if isinstance(created, datetime):
            starts.append(created)
        if isinstance(updated, datetime):
            ends.append(updated)

        if "fee_total" in row and row.get("fee_total") is not None:
            fee_rows += 1
            ccy = str(row.get("fee_currency") or "UNKNOWN")
            fee_totals[ccy] = fee_totals.get(ccy, Decimal(0)) + (
                _dec(row["fee_total"], "fee_total") or Decimal(0)
            )

        counts.add(status, quantity, done)

        weighted, weight, samples = _slippage_sample(row, done, side)
        slip_weighted += weighted
        slip_qty += weight
        slip_n += samples

    unavailable: dict[str, str] = {
        "net_pnl": UNAVAILABLE_NET_PNL,
        "recovery_event_count": UNAVAILABLE_RECOVERY,
    }
    if counts.terminal == 0:
        for key in ("fill_ratio", "partial_fill_ratio", "unfilled_ratio", "reject_ratio"):
            unavailable[key] = UNAVAILABLE_NO_TERMINAL
    # 수수료는 모든 주문에 있을 때만 합계를 낸다 -- 일부만 합하면 과소 보고가 좋은 숫자로 보인다.
    fee_complete = order_count > 0 and fee_rows == order_count
    if fee_rows == 0:
        unavailable["fee_totals"] = UNAVAILABLE_NO_FEE_COLUMN
    elif not fee_complete:
        unavailable["fee_totals"] = UNAVAILABLE_PARTIAL_FEE
    avg_slippage = (slip_weighted / slip_qty) if slip_qty > 0 else None
    if avg_slippage is None:
        unavailable["avg_slippage_bps"] = UNAVAILABLE_NO_SLIPPAGE_SAMPLE

    start = min(starts) if starts else None
    end = max(ends) if ends else None
    period_days = None
    if start is not None and end is not None and end >= start:
        period_days = Decimal(str((end - start).total_seconds())) / Decimal(86400)
    return LiveReadiness(
        order_count=order_count,
        terminal_count=counts.terminal,
        open_count=counts.open_count,
        unknown_count=counts.unknown_count,
        filled_count=counts.filled,
        partial_fill_count=counts.partial,
        unfilled_count=counts.unfilled,
        rejected_count=counts.rejected,
        fill_ratio=_ratio(counts.filled, counts.terminal),
        partial_fill_ratio=_ratio(counts.partial, order_count),
        unfilled_ratio=_ratio(counts.unfilled, counts.terminal),
        reject_ratio=_ratio(counts.rejected, counts.terminal),
        period_start=start,
        period_end=end,
        period_days=period_days,
        avg_slippage_bps=avg_slippage,
        slippage_sample_size=slip_n,
        fee_totals=dict(fee_totals) if fee_complete else None,
        net_pnl=None,
        recovery_event_count=None,
        unavailable=unavailable,
    )
