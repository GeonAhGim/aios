"""BOM(U+FEFF) 유입 차단 게이트 -- OPS-45(task-3387).

957ae247(외부 엔진 레인 커밋)의 메시지가 UTF-8 BOM으로 시작했고, 그 전에는
tick_parquet.py 등 4개 소스/테스트 파일이 BOM 때문에 AST 기반 게이트
(compliance_gate/position_key_central)를 깼다(5a20db30에서 forward-fix). 둘 다
Claude Code 훅(.claude/hooks/)이 적용되지 않는 codex/cursor 커밋 경로에서 새어 들어왔다.

이 스크립트는 src/tests/scripts/docs 전체와 frontend 아래 각 패키지의 src/ 를 전수
스캔해 BOM으로 시작하는 파일을 찾는다. 래칫이 아니다 -- 한 건이라도 있으면 항상
FAIL(rc=1); 위반 수가 과거보다 늘지 않았다고 봐주는 baseline이 없다.

사용: `python scripts/check_no_bom.py`. 종료코드 0=통과.
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOM = b"\xef\xbb\xbf"
SCAN_ROOT_NAMES = ("src", "tests", "scripts", "docs")
SKIP_DIR_NAMES = {"__pycache__", "node_modules", ".git", "dist", "build", "coverage", ".venv"}


# Cold checkout (fresh CI runner / worktree reset) has no page cache, so each open() blocks on
# physical I/O; a serial walk over ~6-7k tracked files took 60-70s and blew the 60s CI step
# budget (esc-ci-no_bom, 2026-09-26 probe). Threads overlap that I/O latency -- this is I/O-bound
# so the GIL is released during read(), and wall-clock drops well under budget even cold.
#
# 2026-09-29(task-8600): that fix still left find_bom_files() running one ThreadPoolExecutor
# *per root* sequentially (src's pool must fully drain before tests's pool even starts listing),
# so the per-root pools never overlapped each other -- on this fleet's shared, antivirus-scanned
# Windows workers, cold per-file open() latency pushed the still-serialized total past 60s again
# (esc-ci-no_bom kept firing for 4 days after the threading fix merged). Scanning every root's
# files through one shared pool lets all of that I/O-bound waiting overlap across roots too.
#
# 2026-09-29(task-8639/esc-ci-prepare): a fixed SCAN_WORKERS=64 fired 64 real OS threads from
# this one step regardless of how many other lanes were doing the same thing on this shared,
# single-box Windows fleet at once -- concurrent lanes each opening 64 files at a time drove a
# burst of antivirus real-time scanning big enough to starve sibling processes' startup, and a
# git.exe spawned by another lane's local_ci prepare (head_sha()) failed mid-init
# (RuntimeError: "head_sha: origin/main 해석 실패 rc=3221225794"; rc=0xC0000142
# STATUS_DLL_INIT_FAILED, a process-create-time failure, not a git/network error). Falling back
# to ThreadPoolExecutor's own default (min(32, cpu_count+4), the same formula the standard
# library uses for I/O-bound pools) keeps per-root overlap without hard-coding an oversized,
# unbounded-relative-to-the-shared-box thread count.
#
# 2026-09-29(task-8657/esc-ci-prepare): the same head_sha() STATUS_DLL_INIT_FAILED kept firing
# (identical esc-ci-prepare detail_hash) after the task-8639 fix landed and passed QA (task-8590)
# -- library default on this 24-core box is min(32, 24+4)=28, still per-lane, and this fleet runs
# several worker lanes' local_ci prepare concurrently on one shared box, so 28-wide bursts x N
# concurrent lanes reproduced the same antivirus-scan-starves-sibling-process-create symptom the
# 64-wide fix was meant to fix. That change dropped this constant to 8 on the assumption -- based
# on a *warm-cache* local benchmark -- that 8 vs the library default (28) cost only ~0.8s extra on
# ~4.3k files.
#
# 2026-09-29(task-8692/esc-ci-no_bom): that assumption did not hold on a genuinely cold checkout --
# this step itself started timing out again (esc-ci-no_bom, [no_bom] timeout 60s) within minutes of
# the task-8657 commit landing. A cold-cache measurement (first touch of this worktree's ~4.3k
# files, no warmup) showed 8 workers takes ~42s wall-clock -- 70% of the 60s step budget with no
# margin for fleet load, versus <1s once the OS page cache is warm; the earlier "0.8s" delta was
# measured after the files were already warm and does not reflect the cold-checkout case this step
# actually runs under in CI. Raised to 16 -- still half the library default (28) that caused the
# esc-ci-prepare storm, so it does not reintroduce that regression, but doubling the prior value
# gives back most of the cold-checkout margin this step needs.
#
# 2026-09-30(task-8667/esc-ci-no_bom): 16 workers still took ~51s wall-clock on cold checkout --
# further margin compression within 60s budget. Raised to 24, staying below library default (28)
# that caused STATUS_DLL_INIT_FAILED (task-8657 detail). No budget/baseline change
# (DECISION_GUIDELINES B-2) -- this only retunes this step's own concurrency footprint.
#
# 2026-09-30(task-9156/esc-ci-prepare): the task-8667 raise to 24 was speculative -- justified by
# cold-checkout margin on a single lane, with no concurrent-lane measurement -- and it reproduced
# the exact task-8657 failure it cited as the ceiling to stay under: another lane's local_ci
# prepare hit `head_sha: origin/main 해석 실패 rc=3221225794` (STATUS_DLL_INIT_FAILED) while this
# step's 24-wide burst was running. 24 sits close enough to the library default (28) that already
# caused the same antivirus-scan-starves-sibling-process-create failure once; it was never actually
# a safe margin below it. Reverted to 16, the last value with no reported STATUS_DLL_INIT_FAILED
# incident against it. Do not raise this again for cold-checkout margin alone -- that reasoning
# already caused this exact regression twice (28 at task-8639, 24 at task-8667/task-9156). Any
# future raise needs a concurrent-multi-lane measurement, not just single-lane wall-clock.
#
# 2026-09-30(task-9259/[health:ci_red_systemic] prepare 24h 4-leaf repeat, task-8602/8639/8657/
# 9156): those four leaves were never four different violations -- every one retuned this exact
# constant back and forth between two constraints that pull in opposite directions and that this
# single-lane script has no way to observe: raising SCAN_WORKERS buys cold-checkout wall-clock
# margin *for this lane*, lowering it buys antivirus-scan headroom *for sibling lanes sharing this
# box*. The value that is safe right now depends on how many other local_ci lanes happen to be
# running concurrently at the moment this step executes -- only the fleet scheduler
# (pm/local_ci.py, ops-owned, out of this repo's reach) has that information. Hardcoding either
# number as a source constant guarantees it eventually becomes wrong in one direction again, and
# every correction has cost a full commit+leaf cycle just to change one number ops could otherwise
# tune directly per fleet load. AIOS_CI_SCAN_WORKERS lets ops override this without a source
# change; the hardcoded default stays 16, the last value with no STATUS_DLL_INIT_FAILED report
# against it. No budget/baseline relief (DECISION_GUIDELINES B-2) -- this only changes how the
# constant is sourced.
def _resolve_scan_workers(default: int) -> int:
    raw = os.environ.get("AIOS_CI_SCAN_WORKERS")
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


SCAN_WORKERS = _resolve_scan_workers(16)


def has_bom(path: Path) -> bool:
    """파일 선두 3바이트가 UTF-8 BOM(EF BB BF)인지만 본다 -- 내용 중간의 BOM은 대상이 아니다."""
    try:
        with path.open("rb") as fh:
            return fh.read(3) == BOM
    except OSError:
        return False


def _iter_and_scan(base: Path, pool_executor: ThreadPoolExecutor) -> list[Path]:
    """os.walk with in-place pruning and real-time parallel scanning: instead of
    collecting all paths first then scanning, scan files as they are discovered to
    reduce memory pressure on cold checkouts and improve cache locality."""
    if not base.is_dir():
        return []
    bom_files = []
    pending_paths = []

    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
        for name in filenames:
            pending_paths.append(Path(dirpath) / name)
            # Scan in batches to avoid unbounded memory growth and enable
            # early termination on large directories.
            if len(pending_paths) >= 256:
                flags = list(pool_executor.map(has_bom, pending_paths))
                bom_files.extend(
                    [path for path, is_bom in zip(pending_paths, flags, strict=True) if is_bom]
                )
                pending_paths.clear()

    # Scan remaining batch.
    if pending_paths:
        flags = list(pool_executor.map(has_bom, pending_paths))
        bom_files.extend(
            [path for path, is_bom in zip(pending_paths, flags, strict=True) if is_bom]
        )

    return bom_files


def _scan_all(files: list[Path]) -> list[Path]:
    """Scans every listed file through one shared thread pool so I/O-bound waits on a
    cold checkout overlap across scan roots, not just within a single root's files."""
    if not files:
        return []
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:  # None -> library default
        flags = pool.map(has_bom, files)
    return [path for path, is_bom in zip(files, flags, strict=True) if is_bom]


def frontend_src_dirs(repo_root: Path) -> list[Path]:
    """frontend/ 아래 apps/packages 각각의 src/ 디렉터리(예: frontend/apps/web/src,
    frontend/packages/ui-web/src) -- 워크스페이스 구조상 frontend/src 단일 디렉터리는
    존재하지 않는다."""
    frontend = repo_root / "frontend"
    if not frontend.is_dir():
        return []
    # Pruned walk: rglob("src") descended into node_modules (tens of thousands of entries).
    found: set[Path] = set()
    for dirpath, dirnames, _files in os.walk(frontend):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
        if Path(dirpath).name == "src":
            found.add(Path(dirpath))
            dirnames[:] = []  # a src/ tree is scanned by _scan; no nested src lookup needed
    return sorted(found)


def find_bom_files(repo_root: Path) -> list[Path]:
    """repo_root 아래 전체 스캔 대상(SCAN_ROOT_NAMES + frontend의 각 src/)에서 BOM으로
    시작하는 파일 경로를 정렬해 돌려준다. Real-time scanning during traversal
    reduces memory pressure on cold checkouts and improves cache locality."""
    roots = [repo_root / name for name in SCAN_ROOT_NAMES]
    roots += frontend_src_dirs(repo_root)
    bom_files: set[Path] = set()

    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        for root in roots:
            bom_files.update(_iter_and_scan(root, pool))

    return sorted(bom_files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BOM(U+FEFF) 유입 차단 게이트(OPS-45)")
    parser.add_argument("--repo", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서 한글 깨짐 방지

    repo_root = args.repo.resolve()
    bom_files = find_bom_files(repo_root)
    if bom_files:
        print(f"FAIL: BOM(U+FEFF)으로 시작하는 파일 {len(bom_files)}건")
        for p in bom_files:
            try:
                rel = p.relative_to(repo_root)
            except ValueError:
                rel = p
            print(f"  - {rel}")
        return 1

    print(f"OK: BOM 유입 없음 ({repo_root})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
