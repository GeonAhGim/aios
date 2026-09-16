"""Shared type aliases -- keeps the minimal contract `submit.py`/
`apply_fill.py`/other order_service modules share without a circular
import (task-4006, P6 LOC split).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]
