# 파일 범위 분할 제안: task-23992 (QA, CI guards 적색)

## 분석

- 원본 files: `["src/"]` — 디렉터리라 파일 범위로 해석 불가(`unresolved_path_scope`).
- 원본 증상: `[guards] timeout 300s` (esc-ci-guards). 단계는 `ci_recheck.build_steps`의 `guards`
  (`C:\aios\meta\guards\run_guards.py`, 타임아웃 300s)이며 `src/`의 코드 결함이 아니라 fleet 측 Guard 실행의 문제다.
- esc-ci-guards.json은 이미 `status=resolved` ("최신 CI 녹색 c65ab2ea99c6", closed 2026-11-11T12:54:49Z).
- 재현 확인: `run_guards.py --base HEAD~1 --head HEAD`를 현 워크트리에서 실행하면 0.5s에 vetoed=false로 종료.
  즉 상시 지연이 아니라 일시적 타임아웃(대형 diff 또는 머신 부하)이었고 현재는 재현되지 않는다.
- 결론: `src/` 아래에 정정할 파일이 없다. 이 QA 리프를 `src/` 전체로 두면 매번 max_turns로 죽는다.

## 제안 분할 (실제 파일 단위)

1. **리프 A — noop 종결 (권장, 코드 수정 없음)**
   - files: 없음(읽기 전용 확인).
   - 작업: `--status done --noop-reason "esc-ci-guards resolved(c65ab2ea99c6), run_guards 0.5s 녹색 재현 불가"`.
2. **리프 B — 재발 시에만: Guard 타임아웃 원인 계측 (ops, fleet 코드)**
   - files: `C:\aios\meta\guards\architecture_guard.py`, `C:\aios\meta\guards\security_guard.py`
     (worker 직접 수정 금지 — ops task로 요청).
   - 작업: 대형 diff(수백 파일)에서 파일별 git 호출 반복 여부를 계측해 단일 `git diff --name-status`/배치 호출로 교체.
3. **리프 C — 재발 시에만: 단계 타임아웃 근거 확인 (ops)**
   - files: `ci_recheck.py`의 `guards` 단계(300s) — 타임아웃 상향이 아니라 B의 원인 정정이 우선(DECISION_GUIDELINES B-2).

원본 hold 해제·재큐·코드 수정은 하지 않았다.
