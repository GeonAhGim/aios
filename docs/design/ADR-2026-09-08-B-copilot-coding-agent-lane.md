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

### D4. 헤드리스 Copilot CLI도 로컬 엔진으로 쓴다 (2026-09-08 개정 — 사용자가 설치 완료)
Copilot CLI 1.0.83이 설치됐다(`copilot -p <prompt> --allow-all -C <dir>`로 비대화식 실행 실측). 두 레인을 둔다.
- **GitHub 호스팅 레인**(D1): RAM을 쓰지 않는다. 대량·기계적 리프의 1순위.
- **로컬 Copilot 엔진**(`copilot-local` 풀, OPS-3): `claude -p` 자리에 `copilot -p`를 꽂는 두 번째 로컬 엔진. 한도는 분리되지만 RAM은 쓴다.
  한도 감지는 출력 문구·exit code로 하고 `model_limits.json`의 `copilot` 항목으로 기존 fallback 체계에 편입한다.
두 레인 모두 D2 기준과 D3 게이트를 그대로 적용한다. 안전 게이트·마이그레이션·통제면은 여전히 Claude 워커 몫이다.

### D5. 엔진 매트릭스와 라우팅 (2026-09-08 추가 — 사용자가 Codex·Copilot 유료, Cursor 무료 사용을 위임)
실측: Copilot CLI 설치됨. Codex CLI(`codex exec`)와 Cursor 헤드리스(`cursor-agent`)는 미설치(데스크톱 `cursor` 3.18.9만 있음) — OPS-4가 설치·검증한다.

| 엔진 | 과금 | 실행 위치 | 맡기는 일 | 순위 |
|---|---|---|---|---|
| Claude sonnet 워커 | Max 공유 한도 | 로컬 | 안전 게이트·마이그레이션·통제면·명세 밀도 높은 리프 | 필수 영역 전담 |
| Copilot 코딩 에이전트 | Copilot 기본 유료(별도 한도) | GitHub 러너(RAM 0) | 기계적·대량·테스트로 반증되는 리프 | 1순위 |
| Codex CLI (`codex exec`) | ChatGPT 기본 유료(별도 한도) | 로컬 | 테스트 생성·리팩터·타입/주석 감축 같은 코드 일괄 작업 | 2순위 |
| Copilot CLI (`copilot -p`) | Copilot 한도 | 로컬 | Codex 한도 소진 시 대체 | 3순위 |
| Cursor (`cursor-agent -p`) | 무료(소량) | 로컬 | 문서 정합성·소규모 lint 수정만. 한도 도달 시 자동 제외 | 보조 |

라우팅 원칙: (1) D2 기준을 통과하는 리프는 외부 엔진 우선, Anthropic 한도는 필수 영역에 남긴다.
(2) 엔진별 한도는 `model_limits.json`의 엔진 키로 기록해 오케스트레이터가 자동으로 다음 순위로 넘긴다.
(3) 어떤 엔진 출력도 D3 게이트(로컬 CI 16종 / Actions) 없이 main에 들어가지 않는다.
(4) 엔진별 완료율·재시도율·게이트 실패율을 task JSON에 남겨 2주 뒤 순위를 재평가한다.

## Consequences
- Anthropic 한도와 무관한 병렬 실행력이 생기고, 로컬 RAM 상한과도 무관하다.
- 구현은 ops 풀 task로 한다(OPS-1 레인 구현, OPS-2 파일럿 1건). CA는 결정만 한다(ADR-2026-09-08 위임 원칙).
- 리스크: preview 명령의 인터페이스 변경, Copilot 프리미엄 요청 한도. 둘 다 오케스트레이터 로그와 healthcheck(PR 정체 감지)로 드러나게 한다.

## Rejected
- 워커를 전부 Copilot으로 교체: 안전 게이트·마이그레이션·통제면은 우리 지침을 깊이 아는 워커가 맡아야 한다. 분리 레인이 맞다.
- Copilot PR을 검증 없이 자동 머지: 어떤 에이전트의 출력도 게이트 없이 main에 들어가지 않는다는 원칙과 충돌.

## 파일럿 결과 (2026-09-08, OPS-2 task-2149)

PLT-44 잔여 배치를 `role: copilot` task로 만들어 오케스트레이터가 `gh agent-task create`를 호출하는 전
과정을 관찰하려 했으나, **PR 생성 이전 단계(사전 점검)에서 막혀 파일럿을 시작하지 못했다.**

- 명령 인터페이스: 정상. `gh agent-task create/list/view`는 여전히 동작한다(단, preview라 `list`에
  `--repo`가 없다는 기존 관찰과 동일 — cwd를 대상 저장소로 둬야 한다).
- 권한: **실패.** `gh auth status`와 `gh api user`가 이 실행 환경에서 "not logged in"으로 실패한다.
  `GH_TOKEN`/`GITHUB_TOKEN` 환경변수는 설정돼 있지 않고, gh 자체 인증 저장소(호스트 설정)도 비어 있다.
  `gh auth token`은 문자열을 하나 반환하지만(존재 여부·유효성을 검증하지 않는 명령이라는 gh 공식 동작대로),
  그 토큰으로는 실제 API 호출(`gh api user`)이 인증되지 않는다 — 즉 사용 가능한 자격증명이 아니다.
  2026-09-08 본문의 "실측: `gh agent-task create` 사용 가능"은 다른 세션/환경에서 이미 로그인된 상태로
  확인된 것으로 보이며, 그 인증 상태가 이 worktree/워커 실행 환경까지 이어지지 않는다.
- 게이트: 관찰 못 함(PR이 생성되지 않아 Actions Quality Gate에 도달하지 못했다).
- 소요 시간·프리미엄 요청 수: 해당 없음(파일럿이 시작되지 않았다).
- 조치: PLT-44 배치 절단·`role: copilot` task 생성·원본 task-1759 갱신은 보류했다 — 인증 없이 만들면
  오케스트레이터가 매 주기 `gh agent-task create`를 재시도만 하다 계속 실패하는 무의미한 상태가 된다
  (실패 시 task는 `assigned`로 그대로 남는 안전한 실패 모드이긴 하다, `spawn_copilot`이 로그만 남기고
  continue한다).

**PM/CA 결정 필요**: 이 함대의 실제 실행 환경(오케스트레이터가 상주하는 `C:\aios\pm` 프로세스, 혹은 향후
ops/copilot 워커가 도는 환경)에 유효한 GitHub 자격증명을 어떻게 공급할지 — (a) 그 환경에서 `gh auth login`
1회 수행, (b) Copilot coding agent·repo 스코프를 가진 PAT를 `GH_TOKEN`으로 주입, 둘 중 결정해야 파일럿을
재시도할 수 있다.
