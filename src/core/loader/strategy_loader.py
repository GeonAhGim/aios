"""5.2 — Loader.load_strategy_file().

Spec: 03_core_modules_v1.1.md#§3.1

전략 파일 포맷은 JSON — DB의 strategies.fsm_definition(JSONB, 04번)과
동일 표현이라 왕복 변환이 자연스럽다(03번 원문은 포맷을 명시하지 않았음).
"""
from __future__ import annotations

from pathlib import Path

from src.data.models.strategy_fsm import FSMStrategyConfig


def load_strategy_file(path: Path) -> FSMStrategyConfig:
    """전략 정의 JSON 파일을 읽어 FSMStrategyConfig로 검증·반환한다.

    형식 오류(JSON 파싱 실패, 스키마 불일치)는 Pydantic ValidationError로
    그대로 전파한다 — 여기서 예외를 삼키거나 기본값으로 대체하지 않는다.
    """
    return FSMStrategyConfig.model_validate_json(path.read_text(encoding="utf-8"))
