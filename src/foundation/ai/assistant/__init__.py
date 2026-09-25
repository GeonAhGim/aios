"""U-3a -- AI assistant backend (natural language -> AIOS Script generation/
explanation/backtest narration).

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md#U-3,
ADR-2026-09-09-B Decision C, ADR-2026-09-05-A (Agent Gateway).

This package owns a narrow port (`ports/script_provider.py`) for U-3a alone,
while AI-5 (`src/foundation/ai/providers/`, the general-purpose ModelProvider
port -- task-2639, inflight at commit time) does not exist yet. Once AI-5
lands, this port is expected to become a thin adapter over that general port
(not duplication -- there is nothing to delegate to yet).

Never touches execution (order submission) -- no file anywhere in this
package tree imports `src.core.executor`/`src.services.execution_loop`/
`src.exchanges`/`src.foundation.execution_ownership`/`src.foundation.ems`
(static check: tests/adversarial/assistant/test_no_execution_access.py).
"""

from __future__ import annotations
