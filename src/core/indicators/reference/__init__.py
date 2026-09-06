"""IND-7g — 참조 벡터 3자 교차검증 패키지.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.3 IND-7g.
실제 로직은 `verify_all.py`에 있다. 이 파일은 패키지 마커일 뿐 무엇도 재노출하지
않는다 — 순환 임포트를 피하기 위해 `from src.core.indicators.reference import
verify_all`처럼 하위 모듈을 명시적으로 임포트한다.
"""
from __future__ import annotations
