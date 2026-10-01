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

RATCHET-split(task-10863, ADR-2026-09-10-C LOC 규율) — 원래 703줄이던 이
파일의 구현은 책임 단위로 `scripts/canary_bitget_lib/`에 분리됐다
(`config.py` 설정 로더, `guards.py` 하드가드/kill switch, `gate.py`
pre-trade 게이트, `roundtrip.py` 거래소 왕복, `report.py` 리포트). 이
파일은 그 공개 API를 그대로 재노출하고(기존 `tests/unit/scripts/
test_canary_bitget.py`가 `scripts.canary_bitget` 단일 모듈을 참조하는
것을 바꾸지 않기 위해), CLI 오케스트레이션(`run_canary_session`,
`_async_main`, `main`)만 담당한다.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from scripts.canary_bitget_lib.config import (
    CanaryConfigError,
    CanaryLimits,
    load_canary_limits,
)
from scripts.canary_bitget_lib.gate import (
    CanaryAdapter,
    CanarySessionState,
    GateDecision,
    GateVerdict,
    build_rejection_fixtures,
    evaluate_pretrade_gate,
    submit_with_gate,
)
from scripts.canary_bitget_lib.guards import (
    CanaryHardGuardBlockedError,
    KillSwitchActiveError,
    assert_hard_guard_released,
    assert_kill_switch_inactive,
    missing_credential_env_vars,
)
from scripts.canary_bitget_lib.report import CanaryReport, render_report, write_report
from scripts.canary_bitget_lib.roundtrip import (
    AdapterContractViolationError,
    LimitRoundtripResult,
    MarketRoundtripResult,
    far_limit_roundtrip,
    market_order_roundtrip,
    reconcile_three_way,
    run_preflight,
)

__all__ = [
    "CanaryAdapter",
    "CanaryConfigError",
    "CanaryHardGuardBlockedError",
    "CanaryLimits",
    "CanaryReport",
    "CanarySessionState",
    "AdapterContractViolationError",
    "GateDecision",
    "GateVerdict",
    "KillSwitchActiveError",
    "LimitRoundtripResult",
    "MarketRoundtripResult",
    "assert_hard_guard_released",
    "assert_kill_switch_inactive",
    "build_rejection_fixtures",
    "evaluate_pretrade_gate",
    "far_limit_roundtrip",
    "load_canary_limits",
    "market_order_roundtrip",
    "missing_credential_env_vars",
    "reconcile_three_way",
    "render_report",
    "run_canary_session",
    "run_preflight",
    "submit_with_gate",
    "write_report",
]

logger = logging.getLogger(__name__)

_MARKET_ROUNDTRIP_NOTIONAL_USDT = Decimal("5")


# ---------------------------------------------------------------------------
# 오케스트레이션 + CLI
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

    rejected = []
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
