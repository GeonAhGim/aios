"""공유 타입 별칭 — `submit.py`/`apply_fill.py`/기타 order_service 모듈이
순환 임포트 없이 공유하는 최소 계약만 둔다(task-4006, P6 LOC 분할).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]
