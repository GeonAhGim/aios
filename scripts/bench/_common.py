"""Shared helpers for scripts/bench/*.py — percentile math, run metadata, JSON I/O.

Only stdlib + subprocess (git). No DB/network — individual bench scripts opt into
those themselves when the measured operation requires it (e.g. instrument lookup).
"""
from __future__ import annotations

import json
import platform
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def git_sha(repo_root: Path = ROOT) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "unknown"


def machine_info() -> str:
    impl = f"{platform.python_implementation()}-{platform.python_version()}"
    return f"{platform.system()}-{platform.machine()}/{impl}"


def percentile(samples_ms: list[float], pct: float) -> float:
    """Same nearest-rank convention as tests/unit/core/script/test_compile.py's `_p95`."""
    ordered = sorted(samples_ms)
    idx = min(int(len(ordered) * pct), len(ordered) - 1)
    return ordered[idx]


@dataclass(frozen=True)
class BenchResult:
    benchmark: str
    budget_ms: float
    samples: int
    p50_ms: float
    p95_ms: float
    max_ms: float
    passed: bool
    note: str | None = None

    def to_json(self) -> dict[str, Any]:
        payload = {
            "benchmark": self.benchmark,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "git_sha": git_sha(),
            "machine": machine_info(),
            "budget_ms": self.budget_ms,
            "samples": self.samples,
            "p50_ms": round(self.p50_ms, 3),
            "p95_ms": round(self.p95_ms, 3),
            "max_ms": round(self.max_ms, 3),
            "passed": self.passed,
        }
        if self.note is not None:
            payload["note"] = self.note
        return payload


def summarize(
    benchmark: str, samples_ms: list[float], budget_ms: float, *, note: str | None = None
) -> BenchResult:
    p50 = percentile(samples_ms, 0.50)
    p95 = percentile(samples_ms, 0.95)
    mx = max(samples_ms)
    return BenchResult(
        benchmark=benchmark,
        budget_ms=budget_ms,
        samples=len(samples_ms),
        p50_ms=p50,
        p95_ms=p95,
        max_ms=mx,
        passed=p95 <= budget_ms,
        note=note,
    )


def write_result(result: BenchResult, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result.to_json(), indent=2, ensure_ascii=False) + "\n"
    out_path.write_text(text, encoding="utf-8")


__all__ = [
    "ROOT",
    "BenchResult",
    "git_sha",
    "machine_info",
    "percentile",
    "summarize",
    "write_result",
]
