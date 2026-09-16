"""U-15 PERSONAL 모드 리스크 정책 번들 — task-2749.

Spec: ADR-2026-09-09-B Decision C 확장(사용자 개인 운영 곁가지). 1인
운영자용 보수 리스크 번들('personal-conservative'), 일일 손실 자동 kill,
거래소별 소액 상한, 텔레그램 알림, 일일 리포트, PAPER→LIVE 승격
체크리스트를 담는다.
"""

from __future__ import annotations
