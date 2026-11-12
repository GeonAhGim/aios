# 파일 범위 분할 제안: task-23991 (local-qa, consistency 래칫 리뷰 묶음)

## 분석

- 원본 files: `["scripts/check_consistency.py", "src/"]` — `src/`는 디렉터리라 파일 범위로 해석 불가(`unresolved_path_scope`).
- 검토 대상 커밋 `9f3754126` (`fix(consistency): warn on baseline decrease instead of silently keeping it`)의 실제 변경은 3개 파일뿐이다.
  - `scripts/check_consistency.py` (+2/-2)
  - `consistency-baseline.json` (+1/-1)
  - `tests/unit/scripts/test_check_consistency_contracts.py` (+88)
  - `src/` 변경은 0건 — `src/`를 범위에 넣을 이유가 없다(`env_key_undocumented`의 검사 로직은 `scripts/consistency/contracts.py`, `.env.example`을 읽는다).
- 리뷰 발견 1건(기준선 감소 시 자동 갱신 안 됨, `env_key_undocumented` 4->0)은 위 커밋이 경고 경로로 처리했다. 기준선 완화는 없다.
- 따라서 `src/` 전체 스캔은 불필요하고, 리프 하나(3파일)로 충분하다. 분할 시 더 쪼갤 이유도 없다.

## 제안 분할 (실제 파일 단위)

1. **리프 A — 코드·기준선 검증 (QA 본체)**
   - files: `scripts/check_consistency.py`, `consistency-baseline.json` (읽기 전용 대조)
   - 작업: `python scripts/check_consistency.py` 실행해 exit 0, 감소 경고 출력 확인; 기준선 수치가 올라가지 않았는지 `git diff 9f3754126^ 9f3754126 -- consistency-baseline.json`로 확인.
2. **리프 B — 테스트 DoD 대조**
   - files: `tests/unit/scripts/test_check_consistency_contracts.py`
   - 작업: `pytest tests/unit/scripts/test_check_consistency_contracts.py -q -p no:cacheprovider`; negative ≥3, failure-injection 1, perf 단언 1 존재 여부 확인(D2). 부족하면 같은 파일 대신 sibling `_<aspect>.py`로 추가(CLAUDE.md §6-12, `check_code_ratchets.py --near 30` 선확인).
3. **(선택) 리프 C — 게이트 스모크**
   - files: `scripts/check_code_ratchets.py` (실행만)
   - 작업: ruff/mypy는 3파일 범위로만.

## 권고

- 원본 files에서 `src/`를 제거하고 리프 A+B(3파일)를 한 task로 재발행하면 충분하다.
- 부모 커밋은 `9f3754126`로 확정되어 있다(원본 note의 "부모 커밋 확정 후 재리뷰" 조건 충족).
- 이 제안 task는 원본 hold 해제·재큐·코드 수정을 하지 않았다.
