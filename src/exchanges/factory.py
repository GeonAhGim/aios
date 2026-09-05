"""12.2/12.4 — 거래소별 ExchangeAdapter 생성 팩토리 + LIVE 어댑터 차단.

Spec: 02_exchange_adapter_v1.2.md#§2.1, 13_multi_tenancy_auth_v1.4.md#§13.3,
      docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-B(factory.py 행), §4.3, §9 L4-13

거래소마다 필요한 인증 필드 개수가 달라(Bitget: api_passphrase 1개,
KIS: cano/acnt_prdt_cd 2개) exchange_credentials.extra_encrypted 하나로
흡수한다 — 이 팩토리가 각 Adapter 생성자가 요구하는 형태로 풀어서 넘긴다.

L4-13(task-1519, ADR-2026-08-29-E) — `demo_mode=False`(실계정 어댑터)는
환경변수 `AIOS_ALLOW_LIVE_ADAPTER=1`이 **정확히** 그 값으로 있지 않으면
`FrozenZonePaperAdapterBlockedError`로 생성 자체를 거부한다. 이 팩토리는
운영 경로의 유일한 어댑터 생성 지점(`credential_resolver`/
`exchange_credential_service`의 기본 `adapter_factory`, `main.py` 배선)
이며, 그 사실은 `tests/unit/exchanges/test_factory_guard.py`가 AST로
증명한다(I-10). 이 가드는 `@require_paper_sandbox`(메서드 레벨)·
`Executor`의 이중 확인과 **독립된 세 번째 방어선**이고, 완화는 별도
ADR로만 가능하다.

`paper_sim`(L4-23) — 여기서는 등록 훅만 둔다. 훅이 등록되기 전에
`build_adapter("paper_sim", ...)`를 호출하면 명시적
`PaperSimAdapterUnavailableError`(≠ 무음 폴백)로 실패한다.
"""
from __future__ import annotations

import os
from collections.abc import Callable

from src.core.exceptions import FrozenZonePaperAdapterBlockedError
from src.exchanges.bitget.adapter import BitgetAdapter
from src.exchanges.common.adapter import ExchangeAdapter
from src.exchanges.kis.adapter import KISAdapter
from src.exchanges.nh.adapter import NHAdapter

SUPPORTED_EXCHANGES = ("bitget", "kis", "nh", "paper_sim")

LIVE_ADAPTER_ENV = "AIOS_ALLOW_LIVE_ADAPTER"
_LIVE_ADAPTER_ENV_ALLOW_VALUE = "1"

# (api_key, api_secret, extra) -> 어댑터. L4-23이 `register_paper_sim_factory`로
# 등록한다. paper_sim은 정의상 항상 sandbox(`is_sandboxed=True` 상수)이므로
# demo_mode 인자를 받지 않는다.
PaperSimFactory = Callable[[str, str, dict[str, str]], ExchangeAdapter]
_paper_sim_factory: PaperSimFactory | None = None


class UnsupportedExchangeError(Exception):
    """알 수 없는 exchange 값 또는 extra에 필요한 필드가 누락된 경우."""


class PaperSimAdapterUnavailableError(UnsupportedExchangeError):
    """`paper_sim`은 등록된 거래소이지만 어댑터 본체(L4-23)가 아직 훅에
    등록되지 않았다 — 다른 어댑터로 조용히 대체하지 않는다."""


def register_paper_sim_factory(factory: PaperSimFactory) -> None:
    """L4-23 배선 지점. 재등록은 마지막 등록이 이긴다(앱 기동 시 1회)."""
    global _paper_sim_factory
    _paper_sim_factory = factory


def reset_paper_sim_factory() -> None:
    """테스트 격리용 — 운영 코드는 호출하지 않는다."""
    global _paper_sim_factory
    _paper_sim_factory = None


def live_adapter_allowed() -> bool:
    """`AIOS_ALLOW_LIVE_ADAPTER`가 정확히 "1"일 때만 True. "true"/"yes"/
    공백 포함 값은 전부 거부(fail-closed) — 느슨한 truthy 해석은 오타
    하나로 실계정 어댑터를 여는 경로가 된다."""
    return os.environ.get(LIVE_ADAPTER_ENV) == _LIVE_ADAPTER_ENV_ALLOW_VALUE


def _assert_live_adapter_allowed(exchange: str) -> None:
    if live_adapter_allowed():
        return
    raise FrozenZonePaperAdapterBlockedError(
        f"{exchange}: demo_mode=False(실계정 어댑터) 생성은 환경변수 "
        f"{LIVE_ADAPTER_ENV}={_LIVE_ADAPTER_ENV_ALLOW_VALUE} 없이는 차단됩니다"
        "(ADR-2026-08-29-E FROZEN-PAPER-ONLY, L4 §4.3). 해제는 별도 ADR."
    )


def build_adapter(
    exchange: str,
    api_key: str,
    api_secret: str,
    extra: dict[str, str] | None,
    *,
    demo_mode: bool = True,
) -> ExchangeAdapter:
    extra = extra or {}

    # 어떤 거래소 분기보다 먼저 — 미지 거래소/필드 누락 오류로도 이 가드를
    # 건너뛰지 못하게 한다(paper_sim에 demo_mode=False를 넘기는 것도 모순
    # 요청이므로 동일하게 차단).
    if not demo_mode:
        _assert_live_adapter_allowed(exchange)

    if exchange == "bitget":
        try:
            api_passphrase = extra["api_passphrase"]
        except KeyError as exc:
            raise UnsupportedExchangeError(
                "Bitget은 api_passphrase가 필요합니다."
            ) from exc
        return BitgetAdapter(api_key, api_secret, api_passphrase, demo_mode=demo_mode)

    if exchange == "kis":
        try:
            cano = extra["cano"]
            acnt_prdt_cd = extra["acnt_prdt_cd"]
        except KeyError as exc:
            raise UnsupportedExchangeError(
                "KIS는 cano/acnt_prdt_cd가 필요합니다."
            ) from exc
        return KISAdapter(api_key, api_secret, cano, acnt_prdt_cd, is_paper_trading=demo_mode)

    if exchange == "nh":
        try:
            act_no = extra["act_no"]
        except KeyError as exc:
            raise UnsupportedExchangeError("NH는 act_no가 필요합니다.") from exc
        return NHAdapter(api_key, api_secret, act_no, is_paper_trading=demo_mode)

    if exchange == "paper_sim":
        if _paper_sim_factory is None:
            raise PaperSimAdapterUnavailableError(
                "paper_sim 어댑터(L4-23)가 아직 등록되지 않았습니다 — "
                "register_paper_sim_factory()로 배선하기 전에는 생성할 수 없습니다."
            )
        return _paper_sim_factory(api_key, api_secret, extra)

    raise UnsupportedExchangeError(f"지원하지 않는 거래소입니다: {exchange}")
