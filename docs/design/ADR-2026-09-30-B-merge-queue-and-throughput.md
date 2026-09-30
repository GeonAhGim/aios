# ADR-2026-09-30-B: 머지 큐·기계 검증 정정·게이트 캐시 — 함대 처리 효율

## Status
Accepted (2026-09-30, Chief Architect). 사용자 승인 2026-09-30 08:30 "모두 승인"(6항 중 RAM 증설은 사용자 결정 사항으로 보류).

## Context (실측 2026-09-29~30)
- 워커 22개가 main에 시간당 ~10커밋 직접 push. commit CI 20회 중 녹색 5회, full CI 하루 5회 전부 적색(매번 4~6 단계). esc-ci 종결 중앙값 165h, MVP-1 ⑤ 연속 녹색 0일.
- ci_red 정정 1건 = 구현+QA+리뷰 3리프. 24h 발행 380 중 QA/리뷰 109, 로컬 QA 대기 116.
- frontend QA마다 npm ci 5~7분, pm 테스트 3,300개 전체 실행, full CI ~1h. worktree마다 의존성 재구축.
- Claude PM 사이클 20분마다, 대부분 기계적 작업. 이미 구현된 리프 재발행(7924류) 재작업.

## Decision
1. **머지 큐**: 워커는 `wt/<task>` 브랜치에만 push(`git push origin HEAD:main` 금지 → 프롬프트·훅 변경). 통합기(pm `merge_queue.py`, local_ci 인스턴스 규약)가 10~20분마다 대기 브랜치를 base 위에 순서대로 rebase해 배치 1개로 commit CI 1회(필요 시 full). 녹색이면 배치를 main으로 fast-forward, 적색이면 ci_bisect로 범인 브랜치만 반려(task를 needs_decision이 아닌 assigned 재시도 + note)하고 나머지는 재배치. main은 정의상 항상 commit-CI 녹색. GitHub Quality Gate는 그대로 2차 의견.
2. **기계 검증 정정 리프의 QA/리뷰 생략**: [health:ci_red]·ND-2 수정 리프처럼 "실패 단계 재검사 통과"가 완료 증명인 리프는 stage recheck(task-9037) 통과 시 done 확정, QA·리뷰 후속 리프를 만들지 않는다. 예외: S-tier, 마이그레이션, 게이트·기준선 파일 변경(8992 탐지 대상), diff가 대상 단계 밖 파일을 건드린 경우 → 기존 3단 유지.
3. **게이트 캐시·영향 기반 선택**: (a) 공유 의존성 캐시 — frontend node_modules·python venv를 lock 해시 키로 `C:\aios\cache\<hash>`에 한 벌 두고 worktree는 junction/symlink로 연결(무결성 검사는 해시 비교 1회). (b) 변경 영향 기반 테스트 선택을 frontend(vitest --changed 계열·워크스페이스 한정, 8958)와 pm(변경 모듈의 역의존 테스트 파일)에도 적용, full CI에서만 전체.
4. **정기 PM 사이클을 로컬 PM으로**: ADR-2026-09-30-A의 local-pm이 가동되면 orchestrator의 Claude pm_cycle 주기는 20분→하루 2회(검토·명세 판단)로 내리고, 후속 발행·hold 해제·재큐는 local-pm 사이클(20분)이 맡는다.
5. **발행 전 유사도 검사**: validate_issuance에 "같은 files 집합 ∩ ≥50% 이고 DoD 문장 유사도가 높은 done 리프"가 있으면 거부(근거 id 표시). 재작업(7924류) 차단.
6. **RAM 증설(64→128GB)**: 로컬 워커 상한의 실제 병목. 사용자 결정 사항으로 기록만.

## Consequences
- 리프 5건(ops): ① 머지 큐 ② 기계 검증 정정 QA/리뷰 생략 ③ 캐시·영향 기반 선택 ④ PM 주기 전환(8998~9001 선행) ⑤ 발행 유사도 검사.
- 되돌림: 각 항목 플래그(state/flags)로 독립 회수 가능해야 한다.
