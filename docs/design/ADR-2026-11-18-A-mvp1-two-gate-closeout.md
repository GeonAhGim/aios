# ADR-2026-11-18-A: MVP-1 종결 2관문(T 기술 / U 사용자)

## Status
Accepted (2026-11-18, 사용자 승인: D안 두 관문 T/U, G-1 -> MVP-2, G-5 역할 분리, G-9 규칙만). 원 작성: claude-local-5 worker, task-25537.

## Context

- MVP-1 종결 지표는 ADR-2026-09-26-C §2의 개정판(2026-11-06 Amended)을 따른다.
- ADR-2026-09-26-C 개정판 정의: (a) main + CI 3일 연속 녹색, (b) 재오픈 0(3일 창, `kind=ci_failure`), (c) 하루 고유 수습 서명 ≤8건이 3일 연속, (d) J1~J3 e2e 녹색(closeout_check 12항), (e) D? 리프 0.
- ADR-2026-09-24-A Decision 4는 J1~J3 Playwright 녹색을 MVP-1 closeout 13항(`13_user_journeys`)으로 추가한다. 둘을 "J1~J3 e2e는 `13_user_journeys` 항목, 전체 게이트는 13항"으로 정리한다.
- `docs/milestones/MVP-1_CLOSEOUT.md`는 아직 없다. 저장소에 `docs/milestones/` 디렉터리도 없다.
- `docs/audits/JOURNEY_GAPS_2026-11-18.md`는 아직 없다. 이 ADR에서 미해소 갭 수를 적지 않는다.
- 현재 `UX_JOURNEYS.md` §2 기준으로 G-2·G-3·G-5·G-8·G-10·G-11이 J1~J3 후보이고, G-1·G-6·G-7·G-9는 소속 판정이 필요하다. F-1~F-6과 G-4는 이미 해소됐다.

## Decision

### Decision 1 — 관문 T: 기술 종결 게이트

**통과 조건**(ADR-2026-09-26-C §2 지표(a)~(e) 개정판, 전부 충족):

- (a) GitHub Quality Gate: main + 로컬 commit/full CI **3일 연속 녹색**
- (b) 재오픈 0: 3일 창, 집계 기준 `kind=ci_failure`, `reopened_at`이 창 안인 것만 집계
- (c) 하루 고유 수습 서명 ≤8건: 3일 연속. 서명 = 같은 문제로 묶이는 단위. 8건은 2026-10-29~11-04 녹색 복귀 구간의 실측 일 6~10건 중 하한 쪽.
- (d) J1~J3 e2e 녹색: `closeout_check 13_user_journeys` 항목. J1~J3 Playwright 시나리오 전부 그린.
- (e) D? 리프 0: 깊이 미확정 리프 0 — QA 등급 판정 후 D2 이상만 done 인정(안전축 D3).

**게이트 항목 수**: ADR-2026-09-26-C §2(d)는 "closeout_check 12항", ADR-2026-09-24-A Decision 4는 "13항(`13_user_journeys`)" — J1~J3 e2e는 `13_user_journeys` 항목에 포함, 전체 게이트는 13항. (ADR-2026-09-26-C 원문의 12항은 2026-11-06 Amended 시점에도 13항으로 개정된 상태임. 불일치는 Amended 정의 우선.)

**통과 시 동작**:
1. 태그 생성: `milestone/MVP-1-tech`
2. `docs/milestones/MVP-1_CLOSEOUT.md` 생성
3. closeout 홀드 해제 — MVP-2 첫 명세 리프 발행 허용. MVP-2 첫 명세는 내장 전략 엔진(ADR-2026-09-29-A, 현재 Proposed). 이 ADR은 ADR-2026-09-29-A의 Status를 바꾸지 않는다.

### Decision 2 — 관문 U: 사용자 종결 게이트

**조건 1**: `docs/audits/JOURNEY_GAPS_2026-11-18.md`에서 J1~J3에 속한 미해소 갭이 0건. 소속 판정 기준은 본 ADR §3(Decision 3) 참조.

**조건 2**: 체험(UAT) 스택 대상 실서버 e2e 스모크 통과. 기존 J1 e2e는 `mockBackend` 기반이므로 실서버 스모크는 별도 항목. 체험 스택 주소·실행 방법은 확정 전이므로 "확정 전"으로 표기.

**조건 3**: 사용자가 체험 스택에서 J1~J3를 직접 수행하고 `docs/milestones/MVP-1_USER_SIGNOFF.md`에 서명. 서명 전에는 에이전트가 이 파일을 쓰지 않는다.

**통과 시 동작**:
1. 태그 생성: `milestone/MVP-1`
2. `docs/milestones/MVP-1_USER_SIGNOFF.md`에 사용자 서명 기록

### Decision 3 — J4~J6과 U-1~14는 MVP-2 범위

J4~J6과 U-1~14를 관문 U에 넣지 않는다. 근거:

- `ADR-2026-09-24-A` Decision 4 표: J4(감시·복구), J5(리서치·리포트), J6(설정·승인·감사) 전부 "MVP-2"로 명시.
- `UX_JOURNEYS.md` §1: J4~J6 섹션 헤더에 "(MVP-2)" 표기.
- U-1~14는 J4~J6 및 범용 과업에 매핑되므로 동일하게 MVP-2.

관문 U에 포함되지 않는 항목: J4~J6 여정, U-1~14 기능, E2E-2~5.

### Decision 4 — 관문 T 이후 관문 U 작업의 예외 규칙

관문 T 통과 후에도 "새 MVP-1 명세 리프 발행 중지" 규칙(ADR-2026-09-26-C §1, ADR-2026-09-24-A Decision 1)은 유효하다. 단, **갭 해소 리프**는 예외로 둔다.

- 예외 대상: `docs/audits/JOURNEY_GAPS_2026-11-18.md`의 J1~J3 소속 미해소 갭을 해소하는 리프만 발행 가능.
- 금지: 관문 T 이후에도 J4~J6新功能, U-1~14 신규 기능에 대한 MVP-1 명세 리프 발행은 금지.

## Consequences

- 관문 T(`milestone/MVP-1-tech`)와 관문 U(`milestone/MVP-1`)의 태그는 별개다. T는 기술 종결, U는 사용자 종결.
- `docs/ADR_INDEX.md` 등록은 사용자 승인 후 별도로 한다.
- 되돌림: 이 ADR Superseded, 홀드 조건 복원.

## Rejected (대안 비교)

| 안 | 관문 수 | 사용자 서명 필요 | MVP-2 착수 시점 | 위험 |
|---|---|---|---|---|
| A (현재안) | 2관문(T+U) | 예(UAT 서명) | T 통과 후 즉시 | U가 막히면 MVP-2 착수도 지연. 하지만 T→U 병렬 진행으로 완화. |
| B | 1관문(T만) | 아니오 | T 통과 후 즉시 | 사용자 종결 검증 없음. J1~J3 갭이 해결되지 않은 채 MVP-2로 넘어감. |
| C | 1관문(U만) | 예 | U 통과 후 | 기술 안정성(CI 연속 녹색 등)을 게이트로 안 밂. U만 통과하면 기술 부채가 숨어있을 수 있음. |

A/B/C안의 내용은 이 ADR 안에서 정의. 미확인 수치는 없음.

## Risks

- 체험 스택이 준비되지 않으면 관문 U가 막힌다. 이 경우 T는 통과했으나 MVP-1 태그는 생성되지 않음.
- `pm/local_ci.py`가 gate 모드에서 `journeys` 단계를 기록하지 않아 J1 e2e가 거짓으로 적색이 될 수 있음(`UX_JOURNEYS.md` §1 "J1 closeout 적색 재조사"). 이 문제는 PM 영역이라 이 ADR의 범위 밖이며, 해결 전까지 관문 T 판정에 쓰는 CI 리포트는 full 모드여야 한다.
- `docs/milestones/` 디렉터리가 저장소에 존재하지 않으므로, closeout 문서 생성 시 신규 디렉터리 생성이 필요하다.
- ADR_INDEX.md 등록은 수동 작업이므로, 승인 지연 시 색인 누락 가능.
