"""3자 대사(내부 주문 vs 거래소 주문 vs 체결·잔고) 비교 규칙(L4 명세 §2-A, R9).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-A, §9 L4-05.

80번(foundation.reconciliation)의 `classify_item`/`Classification`을 그대로
재사용한다(§2-A "80번 7분류 재사용") — 새 분류 체계를 또 만들지 않는다.
이 모듈은 그 분류를 "주문/체결/잔고 3자 비교"라는 OMS 전용 도메인에
적용하는 규칙만 추가한다.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from src.foundation.reconciliation.contracts.v1 import Classification
from src.foundation.reconciliation.domain.models import MaterialityPolicy
from src.foundation.reconciliation.domain.rules import classify_item
from src.services.oms.contracts.v1_events import Discrepancy, FillEvent
from src.services.oms.contracts.v1_views import OrderView


def _order_id_key(view: OrderView) -> str:
    return str(view.order_id)


def compare_triple(
    internal: Sequence[OrderView],
    provider_orders: Sequence[OrderView],
    provider_fills: Sequence[FillEvent],
    balances: Mapping[str, Decimal],
    ledger_balances: Mapping[str, Decimal],
    policy: MaterialityPolicy,
) -> list[Discrepancy]:
    discrepancies: list[Discrepancy] = []

    internal_by_id = {_order_id_key(o): o for o in internal}
    provider_by_id = {_order_id_key(o): o for o in provider_orders}

    # I11(80번 §2) — provider 쪽이 아예 없는 주문은 0/무시가 아니라 명시적
    # 불일치다. 내부에만 있는 주문(제출은 됐는데 거래소가 모름)과, 거래소에만
    # 있는 주문(내부가 놓친 체결/주문) 둘 다 잡는다.
    for order_id, internal_view in internal_by_id.items():
        if order_id not in provider_by_id:
            discrepancies.append(
                Discrepancy(
                    kind="ORDER_MISSING_AT_PROVIDER",
                    entity_key=order_id,
                    internal_value=internal_view.status.value,
                    provider_value=None,
                    materiality=Classification.MATERIAL_MISMATCH,
                )
            )

    for order_id, provider_view in provider_by_id.items():
        if order_id not in internal_by_id:
            discrepancies.append(
                Discrepancy(
                    kind="ORDER_MISSING_INTERNAL",
                    entity_key=order_id,
                    internal_value=None,
                    provider_value=provider_view.status.value,
                    materiality=Classification.MATERIAL_MISMATCH,
                )
            )

    for order_id in internal_by_id.keys() & provider_by_id.keys():
        internal_view = internal_by_id[order_id]
        provider_view = provider_by_id[order_id]

        if internal_view.status != provider_view.status:
            discrepancies.append(
                Discrepancy(
                    kind="STATUS_MISMATCH",
                    entity_key=order_id,
                    internal_value=internal_view.status.value,
                    provider_value=provider_view.status.value,
                    materiality=Classification.MATERIAL_MISMATCH,
                )
            )

        domain_qty_classification = classify_item(
            internal_view.filled_quantity, provider_view.filled_quantity, policy
        )
        qty_classification = Classification(domain_qty_classification.value)
        if qty_classification != Classification.HEALTHY:
            discrepancies.append(
                Discrepancy(
                    kind="FILLED_QTY_MISMATCH",
                    entity_key=order_id,
                    internal_value=internal_view.filled_quantity,
                    provider_value=provider_view.filled_quantity,
                    materiality=qty_classification,
                )
            )

    for fill in provider_fills:
        fill_order_id = str(fill.order_id) if fill.order_id is not None else None
        if fill_order_id is None or fill_order_id not in internal_by_id:
            discrepancies.append(
                Discrepancy(
                    kind="FILL_MISSING_INTERNAL",
                    entity_key=fill.provider_fill_id,
                    internal_value=None,
                    provider_value=str(fill.quantity),
                    materiality=Classification.MATERIAL_MISMATCH,
                )
            )

    for currency in set(balances) | set(ledger_balances):
        internal_value = ledger_balances.get(currency)
        provider_value = balances.get(currency)
        # I11 — 내부 원장에 아예 없는 통화도 provider_value 유무와 별개로
        # 0으로 단정하지 않는다: internal_value가 None이면 0으로 취급하지
        # 않고 그대로 보고한다(호출부가 "내부 원장 미기록"으로 판단하도록).
        domain_classification = classify_item(internal_value or Decimal(0), provider_value, policy)
        classification = Classification(domain_classification.value)
        if classification != Classification.HEALTHY or internal_value is None:
            discrepancies.append(
                Discrepancy(
                    kind="BALANCE_MISMATCH",
                    entity_key=currency,
                    internal_value=internal_value,
                    provider_value=provider_value,
                    materiality=(
                        classification
                        if internal_value is not None
                        else Classification.MATERIAL_MISMATCH
                    ),
                )
            )

    return discrepancies


def classify(discrepancy: Discrepancy) -> Classification:
    return discrepancy.materiality
