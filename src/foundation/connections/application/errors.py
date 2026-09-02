"""begin/confirm/sync/revoke 커맨드가 공유하는 조회 관련 예외.

한 곳에 모으는 이유: 각 커맨드 파일이 각자 같은 이름의 예외를 따로 정의하면
겉보기엔 같아 보여도 서로 다른 클래스라 라우터의 `except (...)`가 조용히
못 잡는다 — 실제로 이 리프를 작성하며 겪은 실수라 표준화한다.
"""
from __future__ import annotations


class ConnectionNotFoundError(Exception):
    pass


class CrossTenantConnectionAccessError(Exception):
    """73번 TRU-006과 동일 원칙 — 다른 tenant의 connection은 존재 여부도
    흘리지 않고 거부한다(호출부가 404로 통일해 매핑)."""
