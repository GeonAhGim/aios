"""Audit Evidence API 응답 스키마 — HTTP 세부만 여기 두고, 계약 자체는
`src/foundation/evidence/contracts/v1.py`를 감싼다(106번 §2).

쓰기 엔드포인트는 의도적으로 없다 — 감사 이벤트는 다른 bounded context가
자기 커맨드의 부수효과로 내부에서 기록하는 것이지, 사용자가 HTTP로 직접
만들 수 있으면 감사 이력의 무결성 자체가 무의미해진다(79번 §1 "append-only"의
정신은 "아무도 임의로 못 만든다"까지 포함한다)."""
from __future__ import annotations

from src.foundation.evidence.contracts.v1 import AuditEventView, AuditTimelinePage

__all__ = ["AuditEventView", "AuditTimelinePage"]
