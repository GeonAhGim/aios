"""Nightly maintenance pipeline for the aios test environment.

Executes a series of background maintenance tasks (DB cleanup, evidence sync,
etc.) and returns a summary dict so callers can verify every step completed.
"""
from __future__ import annotations

from scripts.cleanup_orphan_test_dbs import main as cleanup_main


def run() -> dict:
    """Run the full nightly pipeline.

    Returns a dict like::

        {
            "results": {
                "cleanup_orphan_test_dbs": {"status": "ok"},
            }
        }
    """
    results: dict = {}

    # --- cleanup_orphan_test_dbs ---
    try:
        cleanup_result = cleanup_main(["--apply"])
        results["cleanup_orphan_test_dbs"] = {
            "status": "ok" if not cleanup_result.get("errors") else "error",
            "detail": cleanup_result,
        }
    except Exception as exc:  # noqa: BLE001  # ratchet-allow: pipeline must report all step failures even if unexpected
        results["cleanup_orphan_test_dbs"] = {"status": "error", "detail": str(exc)}

    return {"results": results}


if __name__ == "__main__":
    import json  # noqa: TID252  # stdlib, kept for CLI convenience

    print(json.dumps(run(), indent=2))
