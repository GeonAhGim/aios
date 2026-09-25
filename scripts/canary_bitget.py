"""L4-30b — Bitget 실계좌 소액 카나리아 왕복 검증.

Spec: task-2750, ADR-2026-08-29-E(Amended 2026-09-09) 하드가드,
      docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-30 계보.

L4-30(`tests/integration/exchanges/bitget/test_live_demo_roundtrip.py`)이
Bitget **데모** 키로 place/get/cancel 왕복을 검증했다면, 이 스크립트는
**실계좌**로 같은 종류의 왕복을 검증한다. 실계좌라는 이유로 다음 하드가드를
코드 레벨로 강제한다(정책 문서상 금지가 아니라 실행되는 코드 자체의 차단,
`src/core/executor/executor.py`의 `FrozenZoneLiveModeBlockedError` 패턴과
동일 원칙):

1. `assert_hard_guard_released()` — `docs/milestones/MVP-1_CLOSEOUT.md`가
   없으면 `CanaryHardGuardBlockedError`. ADR-2026-08-29-E Amended
   2026-09-09 조건(그 파일 존재 + HB-5 사용자 승인)이 둘 다 없으면 실행
   자체가 불가능하다.
2. 실행 전 사용자 승인 — task-2750 decision 필드에 명시적 승인이 있을 때만
   워커가 이 스크립트를 실계좌 키로 호출한다(스크립트 자신은 task 파일을
   모른다 — 이건 스크립트 밖 절차적 게이트다, PROTOCOL.md 참조).
3. kill switch ACTIVE면 `assert_kill_switch_inactive()`가 즉시
   `KillSwitchActiveError`를 던진다.
4. 상한(`config/risk_policy/canary.yaml`)은 코드 상수가 아니라 이 YAML
   번들로 관리하고, `evaluate_pretrade_gate()`가 어댑터 호출 **이전에**
   평가한다 — REJECT면 `submit_with_gate()`가 `adapter.place_order`를
   아예 호출하지 않는다(fail-closed, `tests/unit/scripts/
   test_canary_bitget.py`가 mock 어댑터로 이를 증명한다).

이 스크립트는 `src/core/executor/executor.py`(FROZEN_PAPER_ONLY)를 거치지
않는다 — Executor는 `mode != 'PAPER'`를 무조건 차단하므로(ADR-2026-08-29-E),
실계좌 카나리아는 애초에 그 경로를 타지 않고 `BitgetAdapter`를 직접
호출한다(L4-30 데모 테스트와 동일 패턴). OMS 제출 파이프라인(주문 원장·
포지션 테이블)도 거치지 않으므로, 3단계의 "3-way 대사"는 (a) 주문 조회
엔드포인트(`get_order`), (b) 주문 이력 엔드포인트(`get_order_history`, 별도
API 경로 — 같은 endpoint의 중복 호출이 아니다), (c) 이 스크립트 자신의
로컬 포지션 누적치, 셋을 비교한다 — 내부 원장/포지션 테이블이 아직 없다는
기존 스코프 축소(`three_way_reconciler.py` 문서화 선례)와 같은 이유다.

레드팀 원칙(redaction) — 키 값·서명은 어떤 로그·리포트·예외 메시지에도
보간하지 않는다. 실계좌 API 키는 `BITGET_CANARY_API_KEY`/
`BITGET_CANARY_API_SECRET`/`BITGET_CANARY_API_PASSPHRASE`(conftest.py가
고정값으로 덮어쓰는 `BITGET_API_KEY`류와 별도 이름 — L4-30 데모 테스트의
`BITGET_DEMO_API_KEY` 선례와 동일 원리)로만 읽는다.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from src.core.loader.config_loader import load_config
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.bitget.symbols import to_canonical_symbol
from src.foundation.risk_gate.ports.repository import RiskGateRepository

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "risk_policy" / "canary.yaml"
HARD_GUARD_MILESTONE_PATH = REPO_ROOT / "docs" / "milestones" / "MVP-1_CLOSEOUT.md"
DEFAULT_REPORT_DIR = REPO_ROOT / "docs" / "ops"

CREDENTIAL_ENV_VARS = (
    "BITGET_CANARY_API_KEY",
    "BITGET_CANARY_API_SECRET",
    "BITGET_CANARY_API_PASSPHRASE",
)

_MARKET_ROUNDTRIP_NOTIONAL_USDT = Decimal("5")
_DEFAULT_MIN_NOTIONAL_USDT = Decimal("1")


class CanaryHardGuardBlockedError(Exception):
    """ADR-2026-08-29-E(Amended 2026-09-09) 하드가드 미해제 — 실계좌 실행 차단."""


class CanaryConfigError(Exception):
    """canary.yaml 스키마 위반 또는 필수 환경변수 누락(fail-closed)."""


class KillSwitchActiveError(Exception):
    """kill switch ACTIVE 상태에서 카나리아를 시작하려는 시도 — 즉시 중단."""


class AdapterContractViolationError(Exception):
    """`ExchangeAdapter.place_order()`가 계약(§2 02번 문서)을 어기고
    `exchange_order_id` 없이 반환한 경우 — 이후 get/cancel이 불가능하므로
    조용히 진행하지 않고 즉시 중단한다."""


def _require_exchange_order_id(order: Order) -> str:
    if not order.exchange_order_id:
        raise AdapterContractViolationError("place_order가 exchange_order_id 없이 반환됨")
    return order.exchange_order_id


# ---------------------------------------------------------------------------
# 1) 리스크 정책 번들 로더 (config/risk_policy/canary.yaml)
# ---------------------------------------------------------------------------


class _CanaryConfigV1(BaseModel):
    """personal-conservative.yaml과 동일 원칙 — `extra="forbid"`로 오타·
    미지 키가 조용히 통과하는 것을 막는다(fail-closed)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: int = Field(gt=0)
    max_order_usdt: float = Field(gt=0)
    max_total_usdt: float = Field(gt=0)
    symbols: list[str] = Field(default_factory=list)
    max_fills: int = Field(gt=0)
    stop_on_daily_loss: bool = True


@dataclass(frozen=True)
class CanaryLimits:
    """`config/risk_policy/canary.yaml`의 순수 도메인 표현. `symbols`는
    정규 표기("BTC/USDT")로 정규화해 보관한다 — `Order.symbol`/게이트 평가가
    전부 정규 표기를 쓰기 때문에(거래소 네이티브 표기는 YAML 파일에서만
    쓴다)."""

    name: str
    max_order_usdt: Decimal
    max_total_usdt: Decimal
    symbols: frozenset[str]
    max_fills: int
    stop_on_daily_loss: bool


def load_canary_limits(path: Path | None = None) -> CanaryLimits:
    target = path if path is not None else DEFAULT_CONFIG_PATH
    raw = load_config(target)
    try:
        cfg = _CanaryConfigV1(**raw)
    except Exception as exc:  # noqa: BLE001 — 스키마 위반은 fail-closed로 재포장
        raise CanaryConfigError(f"{target} 스키마 위반: {exc}") from exc
    return CanaryLimits(
        name=cfg.name,
        max_order_usdt=Decimal(str(cfg.max_order_usdt)),
        max_total_usdt=Decimal(str(cfg.max_total_usdt)),
        symbols=frozenset(to_canonical_symbol(s) for s in cfg.symbols),
        max_fills=cfg.max_fills,
        stop_on_daily_loss=cfg.stop_on_daily_loss,
    )


# ---------------------------------------------------------------------------
# 2) 하드가드 / kill switch 사전 점검
# ---------------------------------------------------------------------------


def assert_hard_guard_released(milestone_path: Path | None = None) -> None:
    """ADR-2026-08-29-E Amended 2026-09-09 — 이 파일이 없으면 실계좌 실행
    자체가 구조적으로 불가능하다(사람이 승인해도 우회 불가)."""
    target = milestone_path if milestone_path is not None else HARD_GUARD_MILESTONE_PATH
    if not target.exists():
        raise CanaryHardGuardBlockedError(
            f"실계좌 카나리아 실행 차단 — {target} 없음. ADR-2026-08-29-E"
            "(Amended 2026-09-09) 하드가드 해제 조건(MVP-1_CLOSEOUT.md 존재 +"
            " HB-5 사용자 승인)이 미충족 상태다."
        )


async def assert_kill_switch_inactive(risk_repo: RiskGateRepository, *, tenant_id: UUID) -> None:
    """이 tenant/GLOBAL 범위에 ACTIVE control이 하나라도 있으면 즉시 중단
    (§4.1 I-01 재사용 — 이 스크립트가 별도 게이트를 새로 발명하지 않는다,
    기존 `RiskGateRepository.list_active_controls`를 읽기 전용으로 재사용)."""
    controls = await risk_repo.list_active_controls(tenant_id=tenant_id, include_all_providers=True)
    if controls:
        reasons = ", ".join(f"{c.scope.value}:{c.reason}" for c in controls)
        raise KillSwitchActiveError(f"kill switch ACTIVE({len(controls)}건: {reasons}) — 즉시 중단")


def missing_credential_env_vars() -> list[str]:
    return [name for name in CREDENTIAL_ENV_VARS if not os.environ.get(name)]


# ---------------------------------------------------------------------------
# 3) 로컬 pre-trade 게이트 — 어댑터 호출 이전에 평가(결정론적 규칙만)
# ---------------------------------------------------------------------------


class GateVerdict(str, Enum):
    ALLOW = "ALLOW"
    REJECT = "REJECT"


@dataclass(frozen=True)
class GateDecision:
    verdict: GateVerdict
    reason: str


@dataclass
class CanarySessionState:
    """세션 중 누적치 — 오케스트레이터(`submit_with_gate`)만 갱신한다."""

    cumulative_usdt: Decimal = Decimal("0")
    fills: int = 0


def evaluate_pretrade_gate(
    *,
    symbol: str,
    notional_usdt: Decimal,
    limits: CanaryLimits,
    state: CanarySessionState,
    min_notional_usdt: Decimal = _DEFAULT_MIN_NOTIONAL_USDT,
) -> GateDecision:
    """DoD 4 — 최소 수량 미만·상한 초과·화이트리스트 밖 심볼을 로컬에서
    REJECT한다(거래소로 안 나감). ADR-2026-08-29-E 설계 제약 1(결정론적
    규칙만, LLM/Agent 판단 없음)을 그대로 따른다."""
    if symbol not in limits.symbols:
        return GateDecision(
            GateVerdict.REJECT, f"symbol {symbol} not in whitelist {sorted(limits.symbols)}"
        )
    if notional_usdt < min_notional_usdt:
        return GateDecision(
            GateVerdict.REJECT,
            f"notional {notional_usdt} below min_notional_usdt {min_notional_usdt}",
        )
    if notional_usdt > limits.max_order_usdt:
        return GateDecision(
            GateVerdict.REJECT,
            f"notional {notional_usdt} exceeds max_order_usdt {limits.max_order_usdt}",
        )
    if state.cumulative_usdt + notional_usdt > limits.max_total_usdt:
        return GateDecision(
            GateVerdict.REJECT,
            f"cumulative {state.cumulative_usdt}+{notional_usdt} exceeds "
            f"max_total_usdt {limits.max_total_usdt}",
        )
    if state.fills >= limits.max_fills:
        return GateDecision(GateVerdict.REJECT, f"max_fills {limits.max_fills} reached")
    return GateDecision(GateVerdict.ALLOW, "ok")


def build_rejection_fixtures(limits: CanaryLimits) -> list[tuple[str, str, Decimal]]:
    """DoD 4의 3가지 구체 거부 입력 — (label, symbol, notional_usdt)."""
    symbol = next(iter(limits.symbols)) if limits.symbols else "BTC/USDT"
    outside_whitelist = "ETH/USDT" if symbol != "ETH/USDT" else "SOL/USDT"
    return [
        ("below_min_notional", symbol, _DEFAULT_MIN_NOTIONAL_USDT / Decimal("2")),
        ("exceeds_max_order_usdt", symbol, limits.max_order_usdt + Decimal("1")),
        ("symbol_outside_whitelist", outside_whitelist, limits.max_order_usdt),
    ]


class CanaryAdapter(Protocol):
    """이 스크립트가 실제로 쓰는 어댑터 메서드만 좁혀 선언한다 — mock
    어댑터 테스트가 `BitgetAdapter` 전체를 흉내 낼 필요 없이 이 Protocol만
    구현하면 된다."""

    account_mode: Any

    async def get_balance(self, asset: str | None = None) -> list[Any]: ...

    async def get_ticker(self, symbol: str) -> Any: ...

    async def place_order(self, order: Order) -> Order: ...

    async def get_order(self, order_id: str) -> Order: ...

    async def get_order_history(
        self, symbol: str | None = None, *, limit: int = 100
    ) -> list[Order]: ...

    async def cancel_order(self, order_id: str) -> bool: ...


async def submit_with_gate(
    adapter: CanaryAdapter,
    order: Order,
    *,
    notional_usdt: Decimal,
    limits: CanaryLimits,
    state: CanarySessionState,
) -> tuple[Order | None, GateDecision]:
    """DoD 4의 핵심 배선 — REJECT면 `adapter.place_order`를 절대 호출하지
    않는다. 이 함수 자체가 mock 어댑터 테스트의 증명 대상이다."""
    decision = evaluate_pretrade_gate(
        symbol=order.symbol, notional_usdt=notional_usdt, limits=limits, state=state
    )
    if decision.verdict is GateVerdict.REJECT:
        logger.warning("canary pretrade gate REJECT: %s", decision.reason)
        return None, decision
    placed = await adapter.place_order(order)
    state.cumulative_usdt += notional_usdt
    state.fills += 1
    return placed, decision


# ---------------------------------------------------------------------------
# 4) 사전 점검 (계정 모드 감지 + 거래 권한 확인)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreflightResult:
    account_mode: str
    balances_observed: int


async def run_preflight(adapter: CanaryAdapter) -> PreflightResult:
    """`get_balance()` 성공 자체가 서명 인증 + 계좌 접근 권한 확인이다
    (Bitget 스팟은 잔고 조회도 서명을 요구하므로, 실패 없이 응답이 오면
    이 키가 계정에 유효하게 도달한다는 뜻). 계정 모드는
    `account_mode.account_aware_request()`가 40085 관측 시 자동 전환하므로
    이 함수는 그 결과를 읽기만 한다(task-2514 L4-31)."""
    balances = await adapter.get_balance()
    mode = adapter.account_mode
    mode_value = mode.value if hasattr(mode, "value") else str(mode)
    return PreflightResult(account_mode=mode_value, balances_observed=len(balances))


# ---------------------------------------------------------------------------
# 5) 시장가 30% 이격 지정가 place/get/cancel 왕복
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LimitRoundtripResult:
    exchange_order_id: str
    placed_status: str
    fetched_status: str
    cancelled: bool
    final_status: str


def _client_order_id(tag: str) -> str:
    return f"l4-30b-canary-{tag}-{uuid4().hex[:8]}"


async def far_limit_roundtrip(
    adapter: CanaryAdapter,
    *,
    symbol: str,
    market_price: Decimal,
    quantity: Decimal,
    side: OrderSide = OrderSide.BUY,
) -> LimitRoundtripResult:
    """DoD 2 — 시장가에서 30% 떨어진(매수는 -30%, 매도는 +30%) 지정가라
    체결 위험 없이 place -> get(NEW) -> cancel -> get(CANCELED) 왕복이
    가능하다(L4-30 데모 테스트의 `_SAFE_PRICE` 선례와 동일 원리)."""
    offset = Decimal("0.7") if side is OrderSide.BUY else Decimal("1.3")
    far_price = market_price * offset
    order = Order(
        client_order_id=_client_order_id("far-limit"),
        strategy_id="l4-30b-canary",
        strategy_version="v1",
        symbol=symbol,
        exchange="bitget",
        side=side,
        order_type=OrderType.LIMIT,
        quantity=quantity,
        price=Money(amount=far_price, currency=Currency.USDT),
        asset_class=AssetClass.CRYPTO,
    )
    placed = await adapter.place_order(order)
    exchange_order_id = _require_exchange_order_id(placed)

    fetched = await adapter.get_order(exchange_order_id)
    cancelled = await adapter.cancel_order(exchange_order_id)
    final = await adapter.get_order(exchange_order_id)

    return LimitRoundtripResult(
        exchange_order_id=exchange_order_id,
        placed_status=placed.status.value,
        fetched_status=fetched.status.value,
        cancelled=cancelled,
        final_status=final.status.value,
    )


# ---------------------------------------------------------------------------
# 6) 5 USDT 시장가 매수/매도 왕복 + 3-way 대사
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReconciliationRecord:
    """거래소(get_order) · 거래소 이력(get_order_history, 별도 엔드포인트) ·
    로컬 포지션(이 스크립트 자신의 누적치), 3원 비교. 내부 원장/포지션
    테이블이 아직 없어(모듈 docstring 참조) history_order가 아직
    전파되지 않은 경우는 실패가 아니라 PENDING으로 구분한다(8.3 원칙 —
    모르는 상태를 실패로 단정하지 않는다)."""

    matched: bool
    pending: bool
    exchange_filled_qty: Decimal
    history_filled_qty: Decimal | None
    local_position_qty: Decimal
    notes: str


def reconcile_three_way(
    *,
    live_order: Order,
    history_order: Order | None,
    local_position_qty: Decimal,
    tolerance: Decimal = Decimal("0.000001"),
) -> ReconciliationRecord:
    exchange_qty = live_order.filled_quantity
    if history_order is None:
        return ReconciliationRecord(
            matched=False,
            pending=True,
            exchange_filled_qty=exchange_qty,
            history_filled_qty=None,
            local_position_qty=local_position_qty,
            notes=f"order_history에 아직 미전파(exchange_order_id={live_order.exchange_order_id})",
        )
    history_qty = history_order.filled_quantity
    matched = (
        abs(exchange_qty - history_qty) <= tolerance
        and abs(exchange_qty - local_position_qty) <= tolerance
    )
    notes = (
        "OK"
        if matched
        else f"MISMATCH exchange={exchange_qty} history={history_qty} local={local_position_qty}"
    )
    return ReconciliationRecord(
        matched=matched,
        pending=False,
        exchange_filled_qty=exchange_qty,
        history_filled_qty=history_qty,
        local_position_qty=local_position_qty,
        notes=notes,
    )


@dataclass(frozen=True)
class MarketRoundtripResult:
    buy_order_id: str
    buy_reconciliation: ReconciliationRecord
    sell_order_id: str
    sell_reconciliation: ReconciliationRecord


async def _place_market_and_reconcile(
    adapter: CanaryAdapter,
    *,
    symbol: str,
    side: OrderSide,
    quantity: Decimal,
    local_position_qty: Decimal,
) -> tuple[Order, ReconciliationRecord]:
    order = Order(
        client_order_id=_client_order_id(f"market-{side.value.lower()}"),
        strategy_id="l4-30b-canary",
        strategy_version="v1",
        symbol=symbol,
        exchange="bitget",
        side=side,
        order_type=OrderType.MARKET,
        quantity=quantity,
        asset_class=AssetClass.CRYPTO,
    )
    placed = await adapter.place_order(order)
    exchange_order_id = _require_exchange_order_id(placed)

    live = await adapter.get_order(exchange_order_id)
    history_rows = await adapter.get_order_history(symbol=symbol)
    history_order = next(
        (row for row in history_rows if row.exchange_order_id == exchange_order_id), None
    )
    reconciliation = reconcile_three_way(
        live_order=live, history_order=history_order, local_position_qty=local_position_qty
    )
    return live, reconciliation


async def market_order_roundtrip(
    adapter: CanaryAdapter,
    *,
    symbol: str,
    quantity: Decimal,
) -> MarketRoundtripResult:
    """DoD 3 — 5 USDT 시장가 매수 -> 체결 대사 -> 동일 수량 매도."""
    buy_order, buy_recon = await _place_market_and_reconcile(
        adapter, symbol=symbol, side=OrderSide.BUY, quantity=quantity, local_position_qty=quantity
    )
    sell_qty = buy_order.filled_quantity if buy_order.filled_quantity > 0 else quantity
    sell_order, sell_recon = await _place_market_and_reconcile(
        adapter,
        symbol=symbol,
        side=OrderSide.SELL,
        quantity=sell_qty,
        # 매도는 "방금 매수로 확보한 만큼 전량 청산"이 로컬이 기대하는
        # 수량이다 — 매수 체결량(sell_qty)이 곧 로컬 기대 포지션 변화량.
        local_position_qty=sell_qty,
    )
    return MarketRoundtripResult(
        buy_order_id=buy_order.exchange_order_id or "",
        buy_reconciliation=buy_recon,
        sell_order_id=sell_order.exchange_order_id or "",
        sell_reconciliation=sell_recon,
    )


# ---------------------------------------------------------------------------
# 7) 리포트 생성 — docs/ops/CANARY_<date>.md
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CanaryReport:
    run_date: str
    account_mode: str
    rejected_cases: tuple[GateDecision, ...]
    limit_roundtrip: LimitRoundtripResult | None = None
    market_roundtrip: MarketRoundtripResult | None = None
    notes: str = ""


def render_report(report: CanaryReport) -> str:
    lines = [
        f"# Bitget 실계좌 카나리아 리포트 — {report.run_date}",
        "",
        f"- 계정 모드: `{report.account_mode}`",
        "",
        "## 1. 거부 케이스 (로컬 게이트 REJECT, 거래소로 미전송)",
        "",
    ]
    for decision in report.rejected_cases:
        lines.append(f"- `{decision.verdict.value}` — {decision.reason}")
    lines.append("")

    lines.append("## 2. 지정가(시장가 대비 30% 이격) place/get/cancel 왕복")
    lines.append("")
    if report.limit_roundtrip is None:
        lines.append("- 실행되지 않음")
    else:
        r = report.limit_roundtrip
        lines.extend(
            [
                f"- 주문 id: `{r.exchange_order_id}`",
                f"- place 직후 상태: `{r.placed_status}`",
                f"- get 상태: `{r.fetched_status}`",
                f"- cancel 결과: `{r.cancelled}`",
                f"- cancel 후 상태: `{r.final_status}`",
            ]
        )
    lines.append("")

    lines.append("## 3. 5 USDT 시장가 매수/매도 왕복 + 3-way 대사")
    lines.append("")
    if report.market_roundtrip is None:
        lines.append("- 실행되지 않음")
    else:
        m = report.market_roundtrip
        for label, order_id, recon in (
            ("매수", m.buy_order_id, m.buy_reconciliation),
            ("매도", m.sell_order_id, m.sell_reconciliation),
        ):
            verdict = "MATCH" if recon.matched else ("PENDING" if recon.pending else "MISMATCH")
            lines.extend(
                [
                    f"### {label}",
                    f"- 주문 id: `{order_id}`",
                    f"- 체결가(거래소): `{recon.exchange_filled_qty}`",
                    f"- 체결가(이력): `{recon.history_filled_qty}`",
                    f"- 로컬 포지션: `{recon.local_position_qty}`",
                    f"- 대사 결과: {verdict} — {recon.notes}",
                    "",
                ]
            )

    if report.notes:
        lines.extend(["## 4. 비고", "", report.notes, ""])

    return "\n".join(lines) + "\n"


def write_report(report: CanaryReport, *, out_dir: Path | None = None) -> Path:
    target_dir = out_dir if out_dir is not None else DEFAULT_REPORT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"CANARY_{report.run_date}.md"
    path.write_text(render_report(report), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 8) 오케스트레이션 + CLI
# ---------------------------------------------------------------------------


async def run_canary_session(
    adapter: CanaryAdapter,
    limits: CanaryLimits,
    *,
    market_price: Decimal,
) -> CanaryReport:
    preflight = await run_preflight(adapter)
    symbol = next(iter(limits.symbols))
    state = CanarySessionState()

    rejected: list[GateDecision] = []
    for _label, bad_symbol, bad_notional in build_rejection_fixtures(limits):
        decision = evaluate_pretrade_gate(
            symbol=bad_symbol, notional_usdt=bad_notional, limits=limits, state=state
        )
        rejected.append(decision)

    limit_quantity = (Decimal("2") / market_price).quantize(Decimal("0.000001"))
    limit_result = await far_limit_roundtrip(
        adapter, symbol=symbol, market_price=market_price, quantity=limit_quantity
    )

    market_notional = _MARKET_ROUNDTRIP_NOTIONAL_USDT
    market_quantity = (market_notional / market_price).quantize(Decimal("0.000001"))
    gate_decision = evaluate_pretrade_gate(
        symbol=symbol, notional_usdt=market_notional, limits=limits, state=state
    )
    if gate_decision.verdict is not GateVerdict.ALLOW:
        raise CanaryConfigError(
            f"5 USDT 시장가 왕복이 로컬 게이트에서 REJECT됨: {gate_decision.reason} "
            "(canary.yaml 상한을 재확인)"
        )
    state.cumulative_usdt += market_notional
    state.fills += 1

    market_result = await market_order_roundtrip(adapter, symbol=symbol, quantity=market_quantity)
    state.fills += 1

    return CanaryReport(
        run_date=datetime.now(timezone.utc).date().isoformat(),
        account_mode=preflight.account_mode,
        rejected_cases=tuple(rejected),
        limit_roundtrip=limit_result,
        market_roundtrip=market_result,
    )


async def _async_main(args: argparse.Namespace) -> int:
    assert_hard_guard_released()

    missing = missing_credential_env_vars()
    if missing:
        raise CanaryConfigError(
            "카나리아 실행 차단 — 누락된 환경변수: "
            f"{', '.join(missing)}(값 자체는 절대 출력하지 않음, redaction)"
        )

    import asyncpg

    from src.exchanges.bitget.adapter import BitgetAdapter
    from src.foundation.risk_gate.adapters.postgres_repository import (
        PostgresRiskGateRepository,
    )

    limits = load_canary_limits(Path(args.config) if args.config else None)

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    try:
        risk_repo = PostgresRiskGateRepository(pool)
        await assert_kill_switch_inactive(risk_repo, tenant_id=UUID(args.tenant_id))

        adapter = BitgetAdapter(
            os.environ["BITGET_CANARY_API_KEY"],
            os.environ["BITGET_CANARY_API_SECRET"],
            os.environ["BITGET_CANARY_API_PASSPHRASE"],
            demo_mode=False,
        )
        try:
            await adapter.sync_server_time()
            symbol = next(iter(limits.symbols))
            ticker = await adapter.get_ticker(symbol)
            report = await run_canary_session(adapter, limits, market_price=ticker.price)
        finally:
            await adapter.aclose()
    finally:
        await pool.close()

    path = write_report(report)
    logger.info("canary report written: %s", path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None, help="canary.yaml 경로 override")
    parser.add_argument("--tenant-id", required=True, help="kill switch 조회용 tenant UUID")
    args = parser.parse_args(argv)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
