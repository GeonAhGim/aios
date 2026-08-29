"""15.3 — 위험등급-전략 매칭 경고 (RiskMatching).

Spec: 기능설계문서_v1.20.md#FD-15.3, 10.3-B 면책조항, 9.4

강제 차단이 아니다(2026-08-10 확정 용도: ①조언 참고 ②불일치 시 경고) —
불일치를 알리기만 하고, 진행 여부는 호출부가 명시적 동의(acknowledged)
로 받는다.

3개 지점에 훅으로 연동한다:
①마켓플레이스 구매(FD-13.3, PurchaseService.check_risk_warning) —
  strategies.risk_level(9.4 MDD/VaR 등 백테스트 기반 — 실제 백테스트
  엔진이 아직 없어 Draft nullable, 13.8 sharpe_ratio와 동일 원칙) vs
  구매자 risk_profile. risk_level이 없으면(백테스트 미실시) 비교할 근거가
  없어 경고를 생략한다.
②직접 제작 전략 배포 승인(FD-14.3, StrategyBuilderService.transition_lifecycle
  의 APPROVED 전이) — 전략 소유자 자신의 risk_profile vs 그 전략 자신의
  risk_level.
③ApprovalMode 변경(FD-11.3, ApprovalSettingsService.update) — 해석
  (FD 원문이 구체적 대조 기준을 명시하지 않아 이번에 정함): SOLO는
  2인 확인 없는 즉시 승인이라 "공격형"에, DUAL은 2인 확인을 거치는
  더 안전한 선택이라 "안정형"에 대응시켜 동일 비교기를 재사용한다 —
  DUAL을 고르는 보수적 사용자가 "더 안전한 선택을 했다"는 이유로
  경고를 받는 일이 없도록 두 모드를 이분법 양끝(안정형/공격형)에만
  배치한다("중립형"에 대응하는 승인모드는 없음).
④재평가로 등급이 나빠진 경우(FD-15.2 예외상황) — RiskProfileService.
  save_assessment()가 이제 완성된 FD-16(strategy_executions)을 대조
  대상으로 삼아 RUNNING 실행 중 새 등급과 불일치하는 것을 찾는다
  (find_running_execution_mismatches). 저장 당시엔 FD-16이 없어 반환값만
  주고 미뤄뒀던 부분 — 실제 소비는 suitability 라우터가 담당한다.

FD-15.3 예외상황 — 위험등급 미지정 사용자(FD-15.1이 필수라 이론상
발생하지 않아야 함)가 이 경로에 도달하면 시스템 오류로 취급한다.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

_SEVERITY = {"안정형": 0, "중립형": 1, "공격형": 2}

# FD-15.3 해석 ③ — ApprovalMode를 동일 3단계 어휘로 대응시킨 것(FD 원문 미명시).
APPROVAL_MODE_RISK_LEVEL = {"SOLO": "공격형", "DUAL": "안정형"}


class RiskMatchingError(Exception):
    """FD-15.3 예외상황 — 위험등급 미지정 사용자. 시스템 오류로 취급, 재평가 강제."""


def check_mismatch(user_risk_profile: str, target_risk_level: str | None) -> str | None:
    """target_risk_level이 None이면(비교 근거 없음) 경고를 생략한다."""
    if user_risk_profile not in _SEVERITY:
        raise RiskMatchingError(f"알 수 없는 위험등급입니다: {user_risk_profile}")
    if target_risk_level is None:
        return None
    if target_risk_level not in _SEVERITY:
        raise RiskMatchingError(f"알 수 없는 위험등급입니다: {target_risk_level}")

    if _SEVERITY[target_risk_level] > _SEVERITY[user_risk_profile]:
        return (
            f"회원님의 위험등급({user_risk_profile})보다 위험도가 높은 대상"
            f"({target_risk_level})입니다. 신중히 검토해주세요."
        )
    return None


async def check_purchase_risk_warning(
    pool: asyncpg.Pool, buyer_user_id: UUID, strategy_id: str, strategy_version: str
) -> str | None:
    """PurchaseService(13.4)의 check_risk_warning DI 콜백 시그니처
    (buyer_user_id, strategy_id, strategy_version) -> str | None에 그대로
    연결 가능하도록 pool을 partial 바인딩해 사용한다(app 조립 단계에서
    functools.partial(check_purchase_risk_warning, pool)로 넘긴다)."""
    async with pool.acquire() as conn:
        user_risk_profile = await conn.fetchval(
            "SELECT risk_profile FROM users WHERE user_id = $1", buyer_user_id
        )
        if user_risk_profile is None:
            raise RiskMatchingError(
                "위험등급이 지정되지 않은 사용자입니다 — 재평가가 필요합니다."
            )
        target_risk_level = await conn.fetchval(
            "SELECT risk_level FROM strategies WHERE strategy_id = $1 AND version = $2",
            strategy_id,
            strategy_version,
        )
    return check_mismatch(user_risk_profile, target_risk_level)


async def find_running_execution_mismatches(
    pool: asyncpg.Pool, user_id: UUID, new_risk_profile: str
) -> list[str]:
    """FD-15.2 예외상황 — 재평가 직후 RUNNING 실행 중 새 등급과 불일치하는
    전략 id 목록을 반환한다(중복 제거). 실제 경고 발행은 호출부(suitability
    라우터) 책임 — 이 함수는 순수 조회+대조만 한다."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT e.strategy_id, s.risk_level "
            "FROM strategy_executions e "
            "JOIN strategies s ON s.strategy_id = e.strategy_id AND s.version = e.strategy_version "
            "WHERE e.user_id = $1 AND e.status = 'RUNNING'",
            user_id,
        )
    return [
        row["strategy_id"]
        for row in rows
        if check_mismatch(new_risk_profile, row["risk_level"]) is not None
    ]
