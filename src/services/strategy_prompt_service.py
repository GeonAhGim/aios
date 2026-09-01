"""FD-14.2(신설) — 자연어 프롬프트 기반 전략 생성 (AI, 현재 비활성화).

Spec: ADR-2026-08-29-wallet-marketplace-dual-seller-strategy-authoring.md §3
— 목표기반 마법사(strategy_wizard_service.py)와 병행하기로 결정한 나머지
축. 최종 목표는 자유 텍스트를 Claude(Anthropic)에 전달해
strategy_wizard_service.GeneratedConditions와 동일한 조건 스키마를
받아오는 것이지만, 이 leaf 시점에는 Anthropic API 크레딧이 $0라
(DevEngine/AIOS 프로젝트 공유 메모 참조) 실제 호출이 항상 실패한다.

크레딧 없이 "그럴듯한 가짜 응답"을 만들어 성공한 것처럼 위장하는 대신,
정직하게 미구현 상태를 반환한다(11번 문서 원칙 — 확인 못 하는 상태를
성공으로 위장하지 않는다, 12번/13번에서 반복 적용된 것과 동일 패턴).
크레딧이 채워지면 이 서비스 안에서만 실제 Anthropic 클라이언트 호출로
교체하면 된다 — 라우터/스키마는 이미 최종 형태(generate()의 반환 타입
GeneratedConditions 그대로).
"""
from __future__ import annotations

from src.services.strategy_wizard_service import GeneratedConditions


class PromptGenerationUnavailableError(Exception):
    """Anthropic API 크레딧 미충전 — 라우터가 501로 변환."""


class StrategyPromptService:
    async def generate(self, prompt: str) -> GeneratedConditions:
        raise PromptGenerationUnavailableError(
            "자연어 기반 전략 생성 기능은 아직 비활성화 상태입니다"
            "(Anthropic API 크레딧 충전 대기 중). 목표기반 마법사를 사용해주세요."
        )
