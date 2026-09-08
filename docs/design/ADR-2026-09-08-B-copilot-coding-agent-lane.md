# ADR-2026-09-08-B: GitHub Copilot 코딩 에이전트를 별도 실행 레인으로 붙인다

## Status
Accepted (2026-09-08, Chief Architect). 사용자 지시: "GitHub Copilot도 유료 결제돼 있으니 활용할 수 있으면 활용하라."

## Context
함대의 모든 워커와 CA 세션은 **같은 Anthropic Max 플랜 5시간/주간 한도**를 나눠 쓰고, 로컬 워커 수는 RAM(8~10대)에 묶여 있다.
Copilot 코딩 에이전트는 (1) 과금과 한도가 완전히 분리돼 있고, (2) GitHub 호스팅 러너에서 돌아 로컬 RAM을 쓰지 않으며,
(3) 결과가 PR로 오므로 Actions Quality Gate(PR 트리거 유지)와 guards 잡이 자동으로 검증한다.

실측(2026-09-08): `gh agent-task create [-F 파일] [-b base]`(gh 2.98, preview) 사용 가능, 저장소에 `copilot-swe-agent` 봇이
할당 가능, `main`은 보호 규칙 없음(에이전트 PR 머지 자유). 헤드리스 Copilot CLI(`copilot -p`)는 설치돼 있지 않다.

## Decision

### D1. `copilot` 풀을 신설한다 — 실행은 GitHub, 조율은 오케스트레이터
- spawn: task 명세를 파일로 만들어 `gh agent-task create -F <file> -b main` (cwd=`C:\aios\aios`). 반환된 세션/PR을 task JSON에 기록.
- reap: PR 상태를 폴링. Actions 체크 전부 녹색 → `gh pr merge --squash --delete-branch` → 머지 커밋으로 `done`.
  체크 적색 → 실패 요약을 PR 코멘트로 `@copilot`에 전달해 재시도(최대 2회) → 초과 시 `needs_decision`.
- 풀 크기 2. RAM 가드 대상이 아니다(로컬 프로세스 없음). 모델 한도(`model_limits.json`)와도 무관하다.

### D2. Copilot에 보내는 리프의 기준
보낸다: 명세가 자기완결적이고 테스트로 반증되는 **기계적·대량** 작업 — 계약 테스트 생성, 지표 스펙 생성, 화면 배선,
`type: ignore`·한글 주석 감축, 오탐 정리, 문서 정합성.
보내지 않는다: FROZEN·FROZEN_PAPER_ONLY 접점, 마이그레이션(직렬화 대상), 안전 게이트·LIVE 가드, `pm/`·`meta/`(통제면), 사람 결정이 섞인 것.
PM은 `role: copilot`으로 배정하고 명세에 "이 저장소의 CLAUDE.md·INVARIANTS.md·`docs/specs` 리프 DoD를 따른다"를 넣는다.

### D3. 검증은 우리 게이트가 한다
Copilot의 자체 판단을 신뢰하지 않는다. 머지 조건은 Actions Quality Gate(ruff·mypy·pytest·coverage·secret scan·guards) 전부 통과다.
머지 후 로컬 CI가 main에서 한 번 더 돈다(게이트 16종). 가능하면 PR에 Copilot 코드 리뷰도 요청한다(가용성 확인 후).

### D4. 헤드리스 Copilot CLI는 옵션으로 남긴다
`@github/copilot` CLI를 설치하면 로컬 워커 엔진(`copilot -p`)으로도 쓸 수 있으나 설치는 사용자 확인 후 한다.
GitHub 호스팅 레인이 먼저다 — RAM을 쓰지 않는다는 이점이 더 크다.

## Consequences
- Anthropic 한도와 무관한 병렬 실행력이 생기고, 로컬 RAM 상한과도 무관하다.
- 구현은 ops 풀 task로 한다(OPS-1 레인 구현, OPS-2 파일럿 1건). CA는 결정만 한다(ADR-2026-09-08 위임 원칙).
- 리스크: preview 명령의 인터페이스 변경, Copilot 프리미엄 요청 한도. 둘 다 오케스트레이터 로그와 healthcheck(PR 정체 감지)로 드러나게 한다.

## Rejected
- 워커를 전부 Copilot으로 교체: 안전 게이트·마이그레이션·통제면은 우리 지침을 깊이 아는 워커가 맡아야 한다. 분리 레인이 맞다.
- Copilot PR을 검증 없이 자동 머지: 어떤 에이전트의 출력도 게이트 없이 main에 들어가지 않는다는 원칙과 충돌.
