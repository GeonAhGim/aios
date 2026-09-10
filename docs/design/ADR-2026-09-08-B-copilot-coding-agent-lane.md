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

## Amended (2026-09-10, OPS-34 task-2941) — 레인 v2: dirty PR 처리·격리 경로 제한·풀 재개 조건

**현상**: 인증 문제 해결 후 파일럿이 재개됐고, Copilot PR 4~5건이 "Ready for review" 직전(draft)
상태로 만들어졌으나 그사이 `main`이 빨리 움직여 여러 건이 `mergeable=CONFLICTING`(dirty)이 됐다.
D1의 원래 폴링 로직은 dirty를 별도로 다루지 않고 체크 결과만 봤기 때문에, 체크가 아예 돌지 않는
draft PR은 `pr_stale_hours`(기본 3h) 타임아웃으로 로컬 레인 전환만 반복했다 — PR 자체는 방치되고
(자동 close 없음, D1 원문 그대로), Copilot 세션 비용만 반복 소모했다.

### A1. dirty(mergeable=CONFLICTING) 전용 분기 추가
`reap_copilot`이 `gh pr view`의 `mergeable` 필드가 `CONFLICTING`인 OPEN PR을 만나면, 기존처럼
체크 결과를 기다리지 않고 `gh pr update-branch`를 **1회만** 시도한다.
- 성공(rc=0): 새 커밋에서 체크가 다시 돌아야 하므로 이번 폴링에서는 merge/close 없이 대기한다.
- 실패(rc≠0, 즉 진짜 충돌이라 자동 병합 불가) 또는 이미 한 번 시도했는데도 여전히 dirty: PR을
  사유를 담은 코멘트와 함께 `gh pr close`하고, 파일 경로로 추정한 원래 축(frontend/ 접두면
  frontend, 아니면 backend)으로 되돌린다(`_copilot_close_and_revert`). `pr_stale_hours` 타임아웃을
  기다리지 않는다 — dirty는 시간이 지나도 저절로 안 풀리는 상태이기 때문이다.
- D3(우리 게이트가 검증)는 그대로 유지: update-branch로 살아난 PR도 정상 체크 통과 후에만
  기존 merge 경로(D1)를 탄다.

### A2. 격리 경로 리프만 배정 (`tiers.yaml: isolated_paths`)
D2("보낸다: 기계적·대량 작업")를 구체적인 판정 규칙으로 좁힌다. `orchestrator.is_isolated_leaf()`가
`tiers.yaml`의 `isolated_paths`(`frontend/*`·`docs/*`·`tests/*`)와 대조해, 리프의 `files` 전부가
그 패턴에 맞거나(예외: 파일이 정확히 1개고 저장소에 아직 없는 신규 파일 — "단일 모듈 신규 파일")
아니면 copilot 레인 배정을 거부한다. `spawn_copilot`은 gh를 부르기 전에
`reroute_non_isolated_copilot`으로 기준을 벗어난 task를 먼저 로컬 레인으로 되돌린다 — base가 빨리
움직이는 기존 src 파일을 copilot에 보내는 것 자체가 A1이 다루는 dirty 발생의 주 원인이었다.

### A3. 풀 재개 조건
CTO가 반복되는 dirty/재시도 낭비 때문에 `pools.yaml`의 `copilot.size`를 0으로 내렸다. A1·A2 구현과
단위테스트 통과를 재개 1단계 조건으로 삼아 size 1로 올린다. 이후 24시간 관찰한 머지율
(`merged / (merged + closed)`, dirty-close 포함)이 50% 이상이면 size 2로 올린다. 50% 미만이면
size를 다시 0으로 내리는 task를 발행하고 원인(격리 경로 판정 누락·update-branch 실패 패턴 등)을
조사한다.

### 처리 결과 (2026-09-10, task-2941 배정 시점의 실측)
당시 열려 있던 Copilot PR 5건: #9(DC-16 backfill_job)·#10(BT-12 tearsheet)·#11(DSL-14
lexer/parser)·#30(IND-8 dsl_indicator, WIP)·#31(DC-24 provider.py 확장, WIP).
- #9·#10·#11: `mergeStateStatus=DIRTY`(`mergeable=CONFLICTING`), 전부 draft. 대응하는 로컬 task
  (2158·2159·2307)는 이미 `pr_stale_hours` 타임아웃으로 backend 레인으로 전환된 뒤 완료
  (QA·Review·DEEPEN 후속까지 끝남) — 즉 이 PR들의 작업은 로컬에서 이미 대체됐다. A1 로직을 수동
  적용해(update-branch를 시도할 가치가 없는, 이미 superseded된 중복 draft라 바로) 사유 코멘트와
  함께 close 처리했다.
- #30·#31: `mergeable=MERGEABLE`이지만 `mergeStateStatus=UNSTABLE`(체크 미완료), 아직 `[WIP]` —
  dirty가 아니고 대응하는 로컬 task(2308·2552)도 아직 시작 전이라 중복이 없다. 그대로 열어 둔다.

## 파일럿 결과 재확인 (2026-09-10, DEEPEN task-3194)

task-2149가 "코드 변경 없음(ADR 문서뿐)"으로 DEEPEN 리프를 받았다 — PLT-44 배치 자체가 한 번도
실제로 생성되지 않아 PR/CI 통과·머지의 실증 증거가 없다는 QA 판정(process-verification 리프).
이번 리프에서 실제로 PLT-44를 20건 단위 배치로 잘라 `role: copilot` task를 새로 만들고 전 과정을
관찰하려 했으나, **2026-09-08 최초 파일럿과 정확히 같은 사전 점검 단계에서 다시 막혔다**:

- 명령 인터페이스: 정상. `gh --version` 2.98.0, `gh agent-task --help`가 `create/list/view`
  서브커맨드를 그대로 보여준다(여전히 preview).
- 권한: **다시 실패.** 이 실행 환경(`C:\aios\wt\ops-1` worker worktree)에서 `gh auth status`·
  `gh api user`가 "not logged in"으로 실패한다. `gh auth token`은 문자열(`gho_...`)을 반환하지만
  그 토큰으로 `gh api user`를 호출해도 인증되지 않는다 — 2026-09-08 노트가 기록한 것과 동일한
  증상. Amended 절(OPS-34, task-2941)이 "인증 문제 해결 후 파일럿이 재개됐다"고 적은 걸 보면
  한 번은 어딘가(오케스트레이터 상주 프로세스 환경일 가능성이 크다)에서 인증이 됐었는데, 그
  상태가 이 worktree/워커 실행 환경까지 이어지지 않는다는 원래 관찰이 그대로 재현됐다 — 즉
  자격증명이 영구적으로(모든 실행 환경에) 공급된 적이 없고, 그때그때 한 프로세스에만 있다가
  사라지는 상태로 보인다.
- 게이트: 다시 관찰 못 함(PR이 생성되지 않았다).
- 소요 시간·프리미엄 요청 수: 해당 없음(파일럿이 또 시작되지 않았다).
- 조치: 원래 파일럿 노트의 판단을 그대로 따라 **PLT-44 배치 절단·`role: copilot` task 생성·
  원본 task-1759 갱신은 이번에도 보류했다** — 인증 없이 만들면 오케스트레이터가 매 주기
  `gh agent-task create`를 재시도만 하다 실패하는 assigned task 하나가 쌓일 뿐, PR/CI 증거는
  여전히 생기지 않는다(실패 모드 자체는 안전하다 — `spawn_copilot`이 로그만 남기고 continue).
  대신 fleet 코드(`orchestrator.py`) 쪽의 실제 결함 하나를 고쳤다: `gh_authenticated()`와
  `spawn_copilot`의 미인증 분기가 "`escalations/esc-copilot-gh-auth.json` 참고"라고 2026-09-08부터
  안내했지만, 그 파일을 실제로 쓰는 코드가 없어서 사람이 로그를 직접 뒤져야만 이 재발을 알 수
  있었다. 이제 미인증이 감지되면 그 파일을 실제로 쓰고(인증 복구 시 자동 삭제) `dashboard.py`가
  이미 읽는 `escalations/*.json` 규약에 얹어 대시보드에 드러나게 했다 — 파일럿 자체를 통과시키진
  못했지만, 다음에 같은 인증 공백이 또 생겼을 때 사람이 더 빨리 알 수 있게 하는 것이 이 리프에서
  낼 수 있는 유일한 실질 진전이라고 판단했다.

**PM/CA 결정 필요(반복)**: 2026-09-08 노트와 동일한 질문이 여전히 해결되지 않았다 — 오케스트레이터
상주 프로세스(`C:\aios\pm`)와 ops/copilot 워커가 실제로 도는 환경에 **영구적인** GitHub 자격증명을
어떻게 공급할지: (a) 그 환경들 각각에서 `gh auth login` 1회 수행, (b) Copilot coding agent·repo
스코프를 가진 PAT를 `GH_TOKEN` 환경변수로 주입(프로세스 재시작에도 유지되도록 시스템 환경변수
또는 서비스 정의에 고정). 이 결정 없이는 PLT-44 파일럿을 세 번째로 재시도해도 같은 지점에서
막힐 것이다.
