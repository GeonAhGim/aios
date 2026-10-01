"""L4-30b — 하드가드 / kill switch / 자격증명 사전 점검.

Spec: task-2750, ADR-2026-08-29-E(Amended 2026-09-09). `scripts/canary_bitget.py`
에서 분리된 책임 단위 — 실계좌 실행 자체를 구조적으로 차단하는 게이트만
담당한다(RATCHET-split task-10863, ADR-2026-09-10-C LOC 규율).
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

from src.foundation.risk_gate.ports.repository import RiskGateRepository

REPO_ROOT = Path(__file__).resolve().parents[2]
HARD_GUARD_MILESTONE_PATH = REPO_ROOT / "docs" / "milestones" / "MVP-1_CLOSEOUT.md"

CREDENTIAL_ENV_VARS = (
    "BITGET_CANARY_API_KEY",
    "BITGET_CANARY_API_SECRET",
    "BITGET_CANARY_API_PASSPHRASE",
)


class CanaryHardGuardBlockedError(Exception):
    """ADR-2026-08-29-E(Amended 2026-09-09) 하드가드 미해제 — 실계좌 실행 차단."""


class KillSwitchActiveError(Exception):
    """kill switch ACTIVE 상태에서 카나리아를 시작하려는 시도 — 즉시 중단."""


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
