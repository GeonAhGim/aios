"""10.3 — 비상 출금 패닉 프롬프트 생성기.

Spec: 기능설계문서_v1.20.md#FD-10.3, 정책문서 7.10-A

카운터파티(거래소) 리스크 심각 신호 감지 시, 사전 등록된 출금 목적지
화이트리스트만으로 프롬프트를 생성한다 — 신규 목적지 입력 경로 자체가
없어, 위기 상황을 가장해 공격자 주소로 유도하는 사회공학 공격을
원천 차단한다(정책문서 20.1-B "위기 이전 준비" 원칙).

최소 2개 독립 소스가 모순 없이 corroborate한 경우에만 신속경로를 연다.
1개뿐이거나 서로 모순되면 확실하지 않은 상황에서 "빠른 길"을 열어주지
않고 FD-10.1 일반 승인 절차로 격하한다.

화이트리스트 조회는 DI 콜백으로 주입받는다(이 세션에서 반복 적용한
패턴). (갱신 — WithdrawalWhitelistService.fetch_for_panic_prompt()가
이제 실제로 이 시그니처를 만족하므로 연결 자체는 가능하다. 다만 이
생성기의 실제 트리거인 "카운터파티 리스크 심각 신호 감지"는 자동
모니터링 파이프라인이 필요한데 그게 아직 없어(Watchdog와 동일한 이유,
FD-9) HTTP 엔드포인트로 노출할 지점이 없다 — corroboration 신호를
누가 채워 넣는지가 스펙에 없는 채로 API를 만들면 추측성 구현이 된다.)
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.core.approval import service as approval

MIN_CORROBORATION_SOURCES = 2


class WhitelistEntry(BaseModel):
    id: int
    exchange: str
    destination_address: str
    label: str | None = None


class CorroborationSignal(BaseModel):
    source: str
    risk_confirmed: bool


class PanicPromptResult(BaseModel):
    fast_path_activated: bool
    destinations: list[WhitelistEntry]
    fallback_approval_request_id: int | None = None


FetchWhitelistFn = Callable[[UUID, str], Awaitable[list[WhitelistEntry]]]


def _corroborates(signals: list[CorroborationSignal]) -> bool:
    """최소 2개 소스가 전원 위험을 확인해야 corroborate로 인정한다 — 1개
    뿐이거나 하나라도 부정하면(모순) 신뢰하지 않는다."""
    if len(signals) < MIN_CORROBORATION_SOURCES:
        return False
    return all(signal.risk_confirmed for signal in signals)


class PanicPromptGenerator:
    def __init__(self, pool: asyncpg.Pool, *, fetch_whitelist: FetchWhitelistFn) -> None:
        self._pool = pool
        self._fetch_whitelist = fetch_whitelist

    async def generate(
        self,
        *,
        user_id: UUID,
        exchange: str,
        corroboration: list[CorroborationSignal],
    ) -> PanicPromptResult:
        if not _corroborates(corroboration):
            request = await approval.create_request(
                self._pool,
                scope="USER",
                user_id=user_id,
                trigger_source="counterparty_risk_panic_uncorroborated",
                requested_action="EMERGENCY_WITHDRAWAL_REVIEW",
                context={
                    "exchange": exchange,
                    "corroboration_sources": [s.source for s in corroboration],
                },
                approval_mode="SOLO",
            )
            return PanicPromptResult(
                fast_path_activated=False,
                destinations=[],
                fallback_approval_request_id=request.id,
            )

        destinations = await self._fetch_whitelist(user_id, exchange)
        return PanicPromptResult(fast_path_activated=True, destinations=destinations)
