"""릴리스 게이트 정적 검사 — PLT-39.

`config/release_gates.yaml`에 정의된 stage별 `required_evidence`(파일 경로)가
저장소에 실제로 존재하는지만 확인한다. DB·네트워크 접근 없음 — CI worktree에
DB가 없어도 동작해야 한다(PLT-38 `check_migration_chain.py`와 같은 정적 방식).

stage는 `depends_on`으로 이전 stage를 가리킨다 — 검사 시 그 stage의
required_evidence도 누적해서 함께 확인한다(103 §8 단계 순서 반영).
LIVE 단계를 정의하더라도 이 스크립트는 판정만 한다 — 어떤 런타임 스위치도
켜지 않는다(ADR-2026-08-29-E).

사용: `python scripts/check_release_gate.py --stage internal_development`.
종료코드 0=통과, 1=설정 오류 또는 미충족 증거 있음.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "release_gates.yaml"


class ReleaseGateConfigError(ValueError):
    """release_gates.yaml 형식 오류 또는 알 수 없는 stage/의존성 순환."""


@dataclass(frozen=True)
class EvidenceItem:
    description: str
    path: str


@dataclass(frozen=True)
class Stage:
    name: str
    depends_on: tuple[str, ...]
    required_evidence: tuple[EvidenceItem, ...]


def load_stages(config_path: Path) -> dict[str, Stage]:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    stages: dict[str, Stage] = {}
    for entry in raw.get("stages", []):
        name = entry["name"]
        evidence = tuple(
            EvidenceItem(description=item["description"], path=item["path"])
            for item in entry.get("required_evidence", [])
        )
        stages[name] = Stage(
            name=name,
            depends_on=tuple(entry.get("depends_on") or []),
            required_evidence=evidence,
        )
    return stages


def _resolve_chain(stages: dict[str, Stage], name: str) -> list[Stage]:
    """`name` 자신 + 모든 선행 stage(`depends_on` 재귀)를 의존 순서로 반환한다."""
    if name not in stages:
        raise ReleaseGateConfigError(
            f"알 수 없는 stage: {name!r} (허용: {', '.join(sorted(stages))})"
        )
    ordered: list[Stage] = []
    seen: set[str] = set()

    def visit(stage_name: str, path: list[str]) -> None:
        if stage_name in path:
            cycle = " → ".join([*path, stage_name])
            raise ReleaseGateConfigError(f"stage depends_on 순환 참조: {cycle}")
        if stage_name in seen:
            return
        stage = stages[stage_name]
        for parent in stage.depends_on:
            if parent not in stages:
                raise ReleaseGateConfigError(
                    f"{stage_name}.depends_on에 알 수 없는 stage: {parent!r}"
                )
            visit(parent, [*path, stage_name])
        seen.add(stage_name)
        ordered.append(stage)

    visit(name, [])
    return ordered


def check_stage(stages: dict[str, Stage], name: str, *, repo_root: Path) -> list[str]:
    """미충족 evidence 설명 목록을 반환한다(빈 목록이면 통과)."""
    missing: list[str] = []
    for stage in _resolve_chain(stages, name):
        for item in stage.required_evidence:
            if not (repo_root / item.path).exists():
                missing.append(f"[{stage.name}] {item.description} — 경로 없음: {item.path}")
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="릴리스 게이트 정적 검사(PLT-39)")
    parser.add_argument("--stage", required=True, help="예: internal_development, internal_paper")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서 한글 깨짐 방지

    if not args.config.is_file():
        print(f"FAIL: {args.config} 없음")
        return 1

    try:
        stages = load_stages(args.config)
        missing = check_stage(stages, args.stage, repo_root=args.repo_root)
    except ReleaseGateConfigError as exc:
        print(f"FAIL: {exc}")
        return 1

    if missing:
        print(f"FAIL: 릴리스 게이트 '{args.stage}' 미충족 항목 {len(missing)}건")
        for line in missing:
            print(f"  - {line}")
        return 1

    print(f"OK: 릴리스 게이트 '{args.stage}' 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
