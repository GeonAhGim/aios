"""6.9/6.10 — KISAdapter Trading 메서드군 + health_check().

Spec: 02_exchange_adapter_v1.2.md#§2.1

엔드포인트(2026-08-28 KIS 공식 GitHub 예제 소스코드 확인):
- POST /uapi/domestic-stock/v1/trading/order-cash, tr_id TTTC0012U(매수)/
  TTTC0011U(매도) — 모의투자 치환은 어댑터가 자동 처리
- POST /uapi/domestic-stock/v1/trading/order-rvsecncl (정정·취소 통합),
  RVSE_CNCL_DVSN_CD로 구분("01"=정정, "02"=취소)
- GET  /uapi/domestic-stock/v1/trading/inquire-daily-ccld, tr_id TTTC0081R
  (최근 3개월 이내 체결조회)

편차: KIS는 주문 취소/정정 시 KRX_FWDG_ORD_ORGNO(거래소전송주문조직번호)와
ORGN_ODNO(원주문번호)가 모두 필요하지만 Order.exchange_order_id는 단일
문자열이다 — place_order()가 "{orgno}:{odno}" 형식으로 합쳐 저장하고,
cancel_order/modify_order가 그 형식을 기대한다(문서화된 편의 규약).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass
from src.data.models.trading import AccountBalance, Order, OrderSide, OrderStatus, OrderType
from src.exchanges.common.adapter import UnsupportedCapabilityError
from src.exchanges.common.http_client import KISHTTPClient
from src.exchanges.common.live_guard import require_paper_sandbox
from src.exchanges.kis.venue_profile import VENUE as _KIS_VENUE
from src.exchanges.kis.venue_profile import register_symbols as _register_kis_symbols
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.reference.symbol_normalizer import to_venue as _to_venue
from src.services.oms.domain.errors import OrderValidationError, UnknownSymbolError
from src.services.oms.domain.rounding import check_notional, require_verified
from src.services.oms.domain.symbol_registry import SymbolRegistry, SymbolSpec

_EXCHANGE_ID = "KRX"  # Phase 1 대상(06번 §6.1)


def _build_kis_symbol_registry() -> SymbolRegistry:
    registry = SymbolRegistry()
    _register_kis_symbols(registry)
    return registry


# task-8337(review task-8182 REJECT follow-up) -- `_precheck_order` used to
# read `VenueCapabilityProfile.price_tick`/`qty_lot`/`min_notional` dicts
# directly, bypassing `SymbolRegistry.spec()`/`require_verified` entirely --
# a symbol explicitly flagged `SymbolSnapshot.verified=False` (step table not
# reconfirmed per price band, e.g. 005930.KS) was still checked against its
# fixed tick with no gate, risking false-positive rejections or missed real
# tick violations in other price bands. Building a module-scoped singleton
# `SymbolRegistry` registered with only KIS symbols forces that path -- it is
# a separate instance from the OMS layer's production registry
# (`wiring.build_production_symbol_registry`), but shares the same snapshot
# source (`venue_profile.register_symbols`), so registered content is
# identical.
_KIS_SYMBOL_REGISTRY = _build_kis_symbol_registry()


def _order_division(order_type: OrderType) -> str:
    return "01" if order_type == OrderType.MARKET else "00"


def _resolve_symbol_spec(registry: SymbolRegistry, venue_symbol: str) -> SymbolSpec | None:
    """`order.symbol` is the bare KRX venue spelling ("005930") passed
    through as-is by `order_dispatch.py` -- translate it back to the
    canonical spelling ("005930.KS") and look it up in `SymbolRegistry`.
    An unregistered symbol returns `None`, exempting it from the check
    (same "unregistered = not subject to the check" convention as the old
    `_lookup_symbol_spec`, task-8074) -- OMS `submit_order()` already
    fail-closed rejects unregistered symbols via `registry.to_venue()`, so
    the only way an unregistered symbol reaches this adapter is a test or an
    operational bypass calling the adapter directly outside the OMS."""
    try:
        canonical = registry.to_canonical(venue_symbol, _KIS_VENUE)
    except UnknownSymbolError:
        return None
    return registry.spec(canonical, _KIS_VENUE)


def _precheck_order(order: Order, registry: SymbolRegistry) -> None:
    """task-8074(AUDIT F4) -- validates tick/lot/min_notional before
    place_order() submits to the exchange (audit finding: this check was
    entirely missing, so orders the exchange would reject were sent
    anyway). task-8337(review task-8182 REJECT follow-up) -- now always
    goes through `SymbolRegistry.spec()`, and a `verified=False` snapshot
    (a value not reconfirmed per price band) is rejected by
    `rounding.require_verified()` (fail-closed, §9 L4-04 DoD c) -- an
    unverified tick is never silently used to allow or deny.
    Skips the check entirely for a symbol missing from the registry -- same
    convention as before (spec §2-A): only registered symbols are checked,
    rather than rejecting unregistered ones."""
    spec = _resolve_symbol_spec(registry, order.symbol)
    if spec is None:
        return
    verified_spec = require_verified(spec)

    if verified_spec.lot > 0 and order.quantity % verified_spec.lot != 0:
        raise OrderValidationError(
            "LOT_MISALIGNED",
            f"수량({order.quantity})이 lot 단위({verified_spec.lot})에 맞지 않습니다: "
            f"{order.symbol}",
        )

    if order.price is None:  # market order -- no tick/min_notional check applies
        return

    price_amount = order.price.amount
    if verified_spec.tick > 0 and price_amount % verified_spec.tick != 0:
        raise OrderValidationError(
            "TICK_MISALIGNED",
            f"가격({price_amount})이 tick 단위({verified_spec.tick})에 맞지 않습니다: "
            f"{order.symbol}",
        )

    check_notional(price_amount, order.quantity, verified_spec.min_notional)


async def _submit_kis_order(client: _OrderSubmittingClient, order: Order, pdno: str) -> Order:
    """task-8338(review task-8184 REJECT follow-up) -- the actual
    order-cash POST + response parsing + `_kis_order_id_map` recording,
    factored out of `place_order` so the map-check and this submission run
    inside the same `_kis_order_id_lock(order.client_order_id)` critical
    section (TOCTOU fix) without duplicating the HTTP body assembly."""
    body: dict[str, Any] = {
        "CANO": client._cano,
        "ACNT_PRDT_CD": client._acnt_prdt_cd,
        "PDNO": pdno,
        "ORD_DVSN": _order_division(order.order_type),
        "ORD_QTY": str(order.quantity),
        "ORD_UNPR": str(order.price.amount) if order.price is not None else "0",
        "EXCG_ID_DVSN_CD": _EXCHANGE_ID,
        "SLL_TYPE": "01" if order.side == OrderSide.SELL else "",
        "CNDT_PRIC": "",
    }
    tr_id = "TTTC0012U" if order.side == OrderSide.BUY else "TTTC0011U"
    raw = await client._request(
        "POST", "/uapi/domestic-stock/v1/trading/order-cash", tr_id, body=body
    )
    # 레드팀 감사(docs/RED_TEAM_FINDINGS.md #18b) 반영 — market_data_mixin과
    # 동일하게 예상 필드 누락을 FatalExchangeError로 통일한다(설명 없는
    # KeyError 대신 어떤 필드가 없었는지 드러낸다).
    try:
        output = raw["output"]
        exchange_order_id = f"{output['KRX_FWDG_ORD_ORGNO']}:{output['ODNO']}"
    except KeyError as exc:
        raise FatalExchangeError(f"KIS 주문 응답에 예상 필드 없음: {exc}") from exc
    if order.client_order_id:
        client._kis_order_id_map()[order.client_order_id] = exchange_order_id
    return order.model_copy(
        update={"exchange_order_id": exchange_order_id, "status": OrderStatus.SUBMITTED}
    )


class ClientOrderIdNotMappedError(FatalExchangeError):
    """task-8079(AUDIT F6) -- raised when a `client_order_id` has no recorded
    KIS ODNO mapping. KIS's REST API has no client_order_id concept at all
    (see the module docstring's ORGNO:ODNO convention), so this adapter
    cannot ask KIS "have you seen this client_order_id before" the way
    Bitget's `find_order_by_client_id` does -- it can only recall what *this
    adapter instance* itself submitted and recorded in `_kis_order_id_map`.
    A missing mapping means one of: the id was never submitted through this
    adapter instance, or it was submitted before a process restart. Either
    way, silently treating it as "new" would resubmit an order that may
    already be live -- fail-closed instead of guessing (DoD 2)."""

    def __init__(self, client_order_id: str) -> None:
        self.client_order_id = client_order_id
        super().__init__(
            f"KIS client_order_id 매핑 없음(신규 주문 아님 보장 불가): {client_order_id!r}"
        )


def _split_exchange_order_id(exchange_order_id: str) -> tuple[str, str]:
    if ":" not in exchange_order_id:
        raise FatalExchangeError(
            f"KIS exchange_order_id는 'orgno:odno' 형식이어야 함: {exchange_order_id}"
        )
    orgno, odno = exchange_order_id.split(":", 1)
    return orgno, odno


class _BalanceCheckingClient(KISHTTPClient, Protocol):
    """health_check()가 같은 어댑터에 조립되는 KISAccountMixin.get_balance()를
    호출하지만, self가 KISHTTPClient로 좁혀진 메서드 안에서는 그 사실이
    보이지 않으므로 명시적으로 계약에 포함한다(bitget _OrderReadingClient와
    동일 패턴)."""

    async def get_balance(self, asset: str | None = None) -> list[AccountBalance]: ...


class _OrderMutatingClient(KISHTTPClient, Protocol):
    """cancel_order()/modify_order()가 같은 클래스의 _rvsecncl()/get_order()를
    호출한다 — 위와 동일 이유로 명시적으로 계약에 포함한다."""

    async def _rvsecncl(
        self, order_id: str, *, decision: str, quantity: Decimal | None
    ) -> dict[str, Any]: ...

    async def get_order(self, order_id: str) -> Order: ...


class _OrderSubmittingClient(KISHTTPClient, Protocol):
    """place_order() calls KISTradingMixin.symbol_registry(), assembled onto
    the same adapter, for tick/lot/min_notional pre-validation via
    `SymbolRegistry`/`require_verified` (task-8074 AUDIT F4, task-8337
    review task-8182 REJECT follow-up) -- included explicitly in the contract for the
    same reason as the two Protocols above. task-8079(F6) adds get_order() --
    a retried client_order_id resolves to an existing ODNO mapping and
    re-fetches its current state instead of resubmitting. task-8338(review
    task-8184 REJECT follow-up) adds `_unsupported()` and the per-id lock
    accessor for `find_order_by_client_id`'s fail-closed miss path and
    `place_order`'s check-and-set critical section."""

    def symbol_registry(self) -> SymbolRegistry: ...

    async def get_order(self, order_id: str) -> Order: ...

    def _kis_order_id_map(self) -> dict[str, str]: ...

    def _kis_order_id_lock(self, client_order_id: str) -> asyncio.Lock: ...

    def _unsupported(self, capability: str) -> UnsupportedCapabilityError: ...


class KISTradingMixin:
    def symbol_registry(self) -> SymbolRegistry:
        """task-8337(review task-8182 REJECT follow-up) -- makes
        `_precheck_order` go through this `SymbolRegistry` instead of
        `VenueCapabilityProfile`'s raw dicts (module singleton,
        `_build_kis_symbol_registry`). Tests can override this as an
        instance attribute to inject different registered content (e.g. a
        `verified=True` fixture) -- same pattern as
        `adapter.venue_profile = lambda: ...`."""
        return _KIS_SYMBOL_REGISTRY

    def _kis_order_id_map(self) -> dict[str, str]:
        """task-8079(F6) -- per-adapter-instance client_order_id -> KIS
        'orgno:odno' correlation table. This is NOT a new idempotency
        guarantee (KIS's REST API has no client_order_id field to send) --
        it only lets *this adapter instance* recognize a retried
        client_order_id it already saw and recall the ODNO KIS assigned,
        instead of KIS's own OMS-level dedup logic (which does not exist)
        stopping a duplicate submission."""
        store: dict[str, str] | None = getattr(self, "_kis_client_order_id_map", None)
        if store is None:
            store = {}
            self._kis_client_order_id_map = store
        return store

    def resolve_client_order_id(self, client_order_id: str) -> str:
        """task-8079(F6) -- looks up the KIS 'orgno:odno' this adapter
        instance recorded for `client_order_id`. Raises
        `ClientOrderIdNotMappedError` instead of returning `None`/`""` on a
        miss (DoD 2) -- unlike Bitget's `find_order_by_client_id`, a miss
        here does not mean "KIS confirms no such order exists" (KIS was
        never asked), so silently treating it as absent would be a guess."""
        try:
            return self._kis_order_id_map()[client_order_id]
        except KeyError:
            raise ClientOrderIdNotMappedError(client_order_id) from None

    def _kis_order_id_lock(self, client_order_id: str) -> asyncio.Lock:
        """task-8338(review task-8184 REJECT follow-up) -- guards
        `place_order`'s check(`_kis_order_id_map` get)-await(`_request`)-set
        critical section for a given `client_order_id`. Without this, two
        concurrent retries of the same `client_order_id` could both observe
        a miss, both submit to KIS, and both then record a mapping (TOCTOU,
        duplicate submission). Locks are created lazily per adapter
        instance; there is no `await` between the `.get()`/store-and-return
        below, so the single-threaded event loop cannot race on creating the
        lock object itself."""
        locks: dict[str, asyncio.Lock] | None = getattr(self, "_kis_client_order_id_locks", None)
        if locks is None:
            locks = {}
            self._kis_client_order_id_locks = locks
        lock = locks.get(client_order_id)
        if lock is None:
            lock = asyncio.Lock()
            locks[client_order_id] = lock
        return lock

    async def find_order_by_client_id(
        self: _OrderSubmittingClient, client_order_id: str
    ) -> Order | None:
        """task-8338(review task-8184 REJECT follow-up) -- wires the F6
        `_kis_order_id_map` correlation table into the real outbox retry
        path (`outbox_submit.call_submit`'s pre-retry reverse lookup, spec
        §5.4/F14). Before this override, the ABC default always raised
        `UnsupportedCapabilityError` here, and `call_submit` catches exactly
        that exception and returns UNKNOWN("RESEND_UNVERIFIABLE") *before*
        ever calling `place_order` again -- so `place_order`'s own
        `_kis_order_id_map` dedup check (task-8079) was unreachable from a
        reclaimed-order retry (review task-8184 finding: "implemented but not wired").

        `venue_profile.py`'s confirmed `supports_client_order_id=False`
        still holds -- KIS's wire protocol genuinely has no client_order_id
        field, so a *miss* stays fail-closed via `_unsupported()` exactly as
        the ABC default would (returning `None` on miss would assert "the
        exchange confirms it doesn't know this id", which KIS was never
        asked, see the ABC docstring). A *hit*, however, is a fact this
        adapter instance itself recorded when it originally submitted the
        order, so resolving and returning it is safe and enables real
        dedup on retry."""
        mapped = self._kis_order_id_map().get(client_order_id)
        if mapped is None:
            raise self._unsupported("find_order_by_client_id")
        return await self.get_order(mapped)

    @require_paper_sandbox
    async def place_order(self: _OrderSubmittingClient, order: Order) -> Order:
        # F5(task-8077) — route order.symbol through the LA-7 single rule
        # (symbol_normalizer) before it becomes PDNO; an unregistered or
        # malformed symbol is rejected fail-closed with
        # SymbolNormalizationError before the exchange is ever called
        # (same uncaught-propagation contract as Bitget's _to_bitget_symbol).
        pdno = _to_venue(Venue.KIS_KRX, order.symbol)
        _precheck_order(order, self.symbol_registry())
        if not order.client_order_id:
            # empty client_order_id is not a correlation key (test
            # `test_place_order_with_empty_client_order_id_never_dedupes`)
            # -- no lock needed since there is nothing to dedupe against.
            return await _submit_kis_order(self, order, pdno)
        async with self._kis_order_id_lock(order.client_order_id):
            mapped = self._kis_order_id_map().get(order.client_order_id)
            if mapped is not None:
                # task-8079(F6) DoD 1 -- retry of an already-mapped
                # client_order_id re-fetches the existing order instead of
                # submitting a new one to KIS.
                existing = await self.get_order(mapped)
                return existing.model_copy(update={"client_order_id": order.client_order_id})
            return await _submit_kis_order(self, order, pdno)

    async def _rvsecncl(
        self: KISHTTPClient, order_id: str, *, decision: str, quantity: Decimal | None
    ) -> dict[str, Any]:
        orgno, odno = _split_exchange_order_id(order_id)
        body: dict[str, Any] = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": orgno,
            "ORGN_ODNO": odno,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": decision,
            "ORD_QTY": str(quantity) if quantity is not None else "0",
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "N" if quantity is not None else "Y",
            "EXCG_ID_DVSN_CD": _EXCHANGE_ID,
        }
        return await self._request(
            "POST", "/uapi/domestic-stock/v1/trading/order-rvsecncl", "TTTC0013U", body=body
        )

    @require_paper_sandbox
    async def cancel_order(self: _OrderMutatingClient, order_id: str) -> bool:
        raw = await self._rvsecncl(order_id, decision="02", quantity=None)
        return bool(raw.get("rt_cd") == "0")

    @require_paper_sandbox
    async def modify_order(self: _OrderMutatingClient, order_id: str, **kwargs: Any) -> Order:
        quantity = kwargs.get("quantity")
        await self._rvsecncl(order_id, decision="01", quantity=quantity)
        return await self.get_order(order_id)

    async def get_order(self: KISHTTPClient, order_id: str) -> Order:
        """편차: BitgetAdapter.get_order()와 동일 이유로 AIOS 전용 필드
        (strategy_id 등)는 자리표시자 — 호출부가 DB 행과 병합해야 한다."""
        _, odno = _split_exchange_order_id(order_id)
        today = datetime.now(timezone.utc)
        start = (today - timedelta(days=7)).strftime("%Y%m%d")
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            "TTTC0081R",
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "INQR_STRT_DT": start,
                "INQR_END_DT": today.strftime("%Y%m%d"),
                "SLL_BUY_DVSN_CD": "00",
                "PDNO": "",
                "CCLD_DVSN": "00",
                "INQR_DVSN": "00",
                "INQR_DVSN_3": "00",
                "ORD_GNO_BRNO": "",
                "ODNO": odno,
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
                "EXCG_ID_DVSN_CD": _EXCHANGE_ID,
            },
        )
        rows = raw.get("output1", [])
        if not rows:
            raise FatalExchangeError(f"KIS 주문을 찾을 수 없음: order_id={order_id}")
        row = rows[0]

        filled_qty = Decimal(row.get("tot_ccld_qty", "0"))
        ord_qty = Decimal(row.get("ord_qty", "0"))
        if filled_qty == 0:
            status = OrderStatus.ACKNOWLEDGED
        elif filled_qty < ord_qty:
            status = OrderStatus.PARTIALLY_FILLED
        else:
            status = OrderStatus.FILLED

        return Order(
            order_id=uuid4(),
            exchange_order_id=order_id,
            client_order_id="",  # KIS는 client_order_id 개념이 없음(어댑터 docstring 참조)
            strategy_id="",  # 자리표시자 — 호출부가 DB 조회로 채워야 함
            strategy_version="",
            symbol=row.get("pdno", ""),
            exchange="kis",
            side=OrderSide.BUY if row.get("sll_buy_dvsn_cd") == "02" else OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=ord_qty,
            status=status,
            filled_quantity=filled_qty,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            asset_class=AssetClass.KR_EQUITY,
        )

    async def health_check(self: _BalanceCheckingClient) -> bool:
        try:
            await self.get_balance()
            return True
        except Exception:  # noqa: BLE001 — 헬스체크는 어떤 예외든 False로 수렴
            return False
