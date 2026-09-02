"""`.aios-zone` 매니페스트 검증 — 08_test_plan §8.7이 요구했으나 CI에 없던 게이트.

검사 항목:
1. zone 이름은 FROZEN / FROZEN_PAPER_ONLY / SCAFFOLD / OPEN 넷뿐이다.
2. 각 패턴은 실제 파일과 최소 하나 매칭돼야 한다. 예외: `aios/kernel/**`
   (Phase 4 예약 경로, 아직 생성 전)은 "미생성 허용" 목록에 둔다.
   전수감사(docs/FULL_AUDIT_2026-09-02.md §4)에서 `src/core/risk/decision/**`가
   실제 파일(`risk/engine.py`)과 어긋나 있었던 것이 이 검사가 없어서였다.
3. `src/**/*.py` 중 어느 zone에도 속하지 않는 파일이 있으면 실패한다 —
   새 모듈은 반드시 zone을 선언해야 한다.

사용: `python scripts/check_zone_manifest.py` (저장소 루트에서). 종료코드 0=통과.
"""
from __future__ import annotations

import fnmatch
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / ".aios-zone"
VALID_ZONES = ("FROZEN", "FROZEN_PAPER_ONLY", "SCAFFOLD", "OPEN")
ALLOWED_MISSING_PREFIXES = ("aios/kernel/",)
COVERAGE_ROOT = "src"


def _matches(pattern: str, relpath: str) -> bool:
    """`**`를 "0개 이상의 디렉터리"로 해석하는 glob 매칭(fnmatch는 `**`를 `*`와
    동일 취급하므로 직접 처리)."""
    if pattern.endswith("/**"):
        prefix = pattern[: -len("/**")]
        return relpath == prefix or relpath.startswith(prefix + "/")
    return fnmatch.fnmatch(relpath, pattern)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서 한글 깨짐 방지
    if not MANIFEST.exists():
        print(f"FAIL: {MANIFEST} 없음")
        return 1
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    zones: dict[str, list[str]] = manifest.get("zones", {})

    failures: list[str] = []
    for zone in zones:
        if zone not in VALID_ZONES:
            failures.append(f"알 수 없는 zone 이름: {zone} (허용: {', '.join(VALID_ZONES)})")

    tracked = [
        p.relative_to(ROOT).as_posix()
        for p in ROOT.rglob("*")
        if p.is_file()
        and ".git" not in p.parts
        and ".venv" not in p.parts
        and "__pycache__" not in p.parts
        and "node_modules" not in p.parts
    ]

    pattern_owner: dict[str, str] = {}
    for zone, patterns in zones.items():
        for pattern in patterns or []:
            pattern_owner[pattern] = zone
            if any(pattern.startswith(prefix) for prefix in ALLOWED_MISSING_PREFIXES):
                continue
            if not any(_matches(pattern, rel) for rel in tracked):
                failures.append(f"[{zone}] 패턴이 어떤 파일과도 매칭되지 않음: {pattern}")

    uncovered = [
        rel
        for rel in tracked
        if rel.startswith(COVERAGE_ROOT + "/")
        and rel.endswith(".py")
        and not any(_matches(pattern, rel) for pattern in pattern_owner)
    ]
    for rel in uncovered:
        failures.append(f"zone 미선언 소스 파일: {rel}")

    if failures:
        print("FAIL: .aios-zone 매니페스트 검증 실패")
        for line in failures:
            print(f"  - {line}")
        return 1

    # 요약 집계는 "가장 엄격한 zone 우선"(FROZEN > FROZEN_PAPER_ONLY > SCAFFOLD > OPEN)
    # — SCAFFOLD의 src/core/** 같은 상위 패턴이 FROZEN_PAPER_ONLY 하위 경로를
    # 이중 집계하지 않도록.
    counts = dict.fromkeys(VALID_ZONES, 0)
    for rel in tracked:
        for zone in VALID_ZONES:
            if any(_matches(p, rel) for p in (zones.get(zone) or [])):
                counts[zone] += 1
                break
    summary = ", ".join(f"{z}={n}" for z, n in counts.items())
    print(f"OK: .aios-zone 매니페스트 검증 통과 — {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
