# ADR-2026-09-03-A: public 모노레포 `GeonAhGim/aios`로 통합

## Status
Accepted (2026-09-03). 사용자 지시 "저장소를 public으로 새로 만들어서 구조도 깔끔하게 정리해".

## Context
- `mihwa-aios`(백엔드)·`mihwa-aios-frontend`(프론트엔드)·설계문서(`C:\aios\*.md`, claude.ai 프로젝트
  지식)가 세 곳에 흩어져 103번 P0-05(설계→코드 추적성)를 만족하지 못했다.
- private 저장소는 무료 Actions 분 한도와 브랜치 보호 제약이 있었고, 실제로 2026-09-01 이후 CI가
  한 번도 실행되지 못했다(계정 billing 잠금이 근본 원인 — 별도 조치).
- 여러 세션이 같은 clone·같은 인덱스를 공유하던 운영은 커밋 오염 사고(`c017525`/`a77e9e4`)를 냈다.

## Decision
1. 새 public 저장소 `GeonAhGim/aios` 하나로 통합한다. **백엔드는 루트에 유지**(Guard·zone 매니페스트·
   스크립트·worker 프롬프트의 경로를 바꾸지 않기 위해), 프론트엔드는 `frontend/`에 `git subtree`로
   이력을 보존해 병합, 설계문서 원본은 `docs/design/`(`codex/` 포함)로 저장소 안에 둔다.
2. 공개 전 `gitleaks`로 전체 이력을 스캔한다(결과: 백엔드 281커밋·프론트엔드 0건). `.gitattributes`로
   바이너리(docx)를 지정한다.
3. 구 저장소 두 개는 GitHub에서 archive한다. 로컬 `C:\aios\mihwa-aios`는 공용 `.venv` 때문에 당분간 남긴다.
4. 이후 모든 worker는 `C:\aios\aios`의 origin/main에서 **각자 worktree**를 만들어 작업한다.

## Consequences
- CI 한 파일(`quality.yml`)에 verify(백엔드)·frontend·guards 세 job. public이라 분 제한 없음(계정 잠금
  해제 후).
- 코드 인용 경로(`src/...`)는 그대로 유효. 프론트엔드 경로만 `frontend/` 접두어가 붙는다.
- 공개 저장소이므로 시크릿·개인정보는 절대 커밋 금지 — Security Guard P4가 CI·Orchestrator에서 강제.

## Rejected
- `backend/` 하위로 백엔드를 옮기는 완전 대칭 구조: Guard·zone·스크립트·프롬프트 전부 경로 수정이
  필요하고, 그 수정은 사람만 병합할 수 있는 meta 저장소를 거쳐야 해 이전 자체가 지연된다.
- 저장소 3개 유지 + 문서 링크: 추적성 문제가 그대로 남는다.
