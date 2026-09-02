# Claude Code Review and Open Questions v1.0

> 상태: **Cross-review — Codex 확인 대기**
>
> 작성자: DevEngine 세션(별도 Claude Code 세션, `C:\devengine\mihwa-devengine`
> 작업 중). 이 세션은 AIOS 코드를 직접 수정하지 않는다.
>
> 목적: `103_enterprise_architecture_full_audit_and_remediation_brief_v1.0.md`
> 와 `88_red_team_campaign_and_remaining_unknowns_v1.0.md`를 읽고 정리한
> 교차검토와, 이 세션 자신이 이전에 쓴 `mihwa-aios/docs/
> QUALITY_ELEVATION_ROADMAP.md`와의 접점·불일치를 기록한다. Codex가 다음
> 작업에서 참고하거나 반박할 수 있도록 남긴다.
>
> 작성일: 2026-09-01

---

## 1. 103/88번에 대한 평가 — 동의하는 부분

- **P0-05(단일 진입점과 추적성 부재)는 이 세션이 독립적으로 도달한
  결론과 정확히 일치한다.** 이 세션은 오늘 사용자와의 대화 중
  `GeonAhGim/AIOSproject`(18~32번 문서) 존재를 우연히 발견했다 —
  DevEngine 작업만 하던 세션이 이 저장소를 알 방법이 원래 없었다.
  103번이 "Claude Code가 구 문서만 읽고 구현할 위험"을 명시한 건 실제로
  일어날 뻔한 상황을 정확히 짚은 것이다.
- 88번의 레드팀 캠페인 방법론(고립된 실행 환경, "결함 없음 ≠ 안전
  인증", "수정자 본인이 유일한 검증자가 될 수 없다", named owner/evidence
  bundle/postmortem 필수)은 견고하다.
- 103번 §1의 운영 원칙("신규 기능의 양을 늘리는 것보다 P0 보완과
  추적성 확립이 우선")에 전적으로 동의한다.

---

## 2. 열린 질문 1 — 문서 생성 속도 자체가 위험 신호 아닌가 [해결됨 — 2026-09-02]

18~32번(15개)에서 33~103번(70개 추가, 총 88개)까지, 이 세션이 확인한
시간 범위 안에서 급격히 늘어났다. 103번이 "이제부터는 P0 먼저"라고
말하는 것 자체는 옳지만, **그 결론에 도달하기 전에 이미 70개 문서가
생성된 뒤였다** — 자기 인식은 있었지만 사전 제동은 없었다는 뜻이다.

**질문**: 103번 이후에도 신규 numbered 설계 문서 생성을 스스로 멈출
메커니즘이 있는가, 아니면 사용자가 외부에서 명시적으로 "당분간 신규
설계문서 금지, P0 위험등록부와 실제 코드/테스트 증거만"이라고 지시해야
하는가? 이 세션의 판단으로는 후자가 필요해 보인다 — 문서 안의 자기
다짐만으로는 같은 패턴이 재발할 수 있다.

**해결 (사용자 확인, 2026-09-02)**: 명시적 중단 지시는 필요 없었다.
Codex는 103/88번을 쓴 시점에서 이미 스스로 설계 세분화 작업을 멈춘
상태다 — 사용자 설명: "코덱스가 이제 문서를 생성하지는 않아. 오히려
지금 아키텍처에서 세부화하고 구체화하던걸 멈춘상태라서 클로드코드가
구현이 어느정도되면 마저 이어갈 생각이야." 즉 정해진 흐름은
**Codex(설계 세분화) → 일시정지 → Claude Code(구현 진행) → 구현이
누적되면 Codex가 그걸 근거로 설계를 다시 이어감**이다. 문서 생성
속도 자체보다, 지금부터는 "구현이 어느정도 진행됨"을 판단할 기준이
없다는 게 새로운 실질적 공백이다 — 이 세션은 재개 신호로 다음을
제안한다: (a) 103번 P0-01~P0-03 중 최소 1개가 staging에서 검증
가능한 테스트/증거로 종결, 또는 (b) RED_TEAM_FINDINGS.md의 HIGH
등급 항목(Kill Switch 우회, MFA 영구잠금)이 커밋된 수정으로 closed.
둘 중 하나가 실제로 일어나면 그게 Codex 재개의 구체적 트리거가 될 수
있다 — 사용자 판단 필요.

---

## 3. 열린 질문 2 — Feature flag vs 물리적 격리, 순서의 문제인가 양자택일인가

`mihwa-aios/docs/QUALITY_ELEVATION_ROADMAP.md`(이 세션 작성, 우선순위 3)는
LIVE 실행 경로를 런타임 feature flag(`AIOS_LIVE_EXECUTION_ENABLED`,
기본값 false)로 즉시 차단하는 걸 제안했다 — 코드/이력을 보존하면서 오늘
당장 청사진의 "P0/P1=paper only" 약속과 실제 동작을 일치시키는 값싼
조치로 의도했다.

103번 P0-02는 다른 결론이다: "단순 feature flag로는 사용자의 실제
자산을 보호할 수 없다" — PAPER/LIVE를 서로 다른 runtime, network egress
policy, secret scope, database/schema, queue/topic, deployment approval로
물리적으로 분리해야 한다고 요구한다.

**이 세션의 판단**: 둘이 상충한다기보다 **시간축이 다른 조치**로 보인다.
feature flag는 "오늘 배포 가능한 임시 방어선"이고, 103번이 요구하는
물리적 격리는 "완료까지 실제 인프라 작업이 필요한 근본 해법"이다.
feature flag를 먼저 걸어 즉시 위험을 낮추고, 그 뒤에 물리적 격리를
목표로 작업하는 순서를 제안한다 — 다만 feature flag 하나로 P0-02가
"완료"로 표시되는 일은 없어야 한다(103번의 완료조건: "침해·오동작
drill에서 PAPER가 LIVE credential·network·order intent에 접근할 수
없음을 독립 테스트와 로그로 증명"). **Codex의 견해를 듣고 싶다 — 순서
동의하는가, 아니면 feature flag 자체가 거짓 안전감을 줘서 스킵해야
한다고 보는가?**

---

## 4. 열린 질문 3 — `QUALITY_ELEVATION_ROADMAP.md`의 지위

이 세션이 쓴 `mihwa-aios/docs/QUALITY_ELEVATION_ROADMAP.md`(우선순위
1~6)는 103번보다 앞서 작성됐고, 지금은 103번이 훨씬 더 정밀하고
최신이다(P0 4개+P1 4개+P2 5개, 완료조건·release gate까지). 두 "마스터
계획" 문서가 동시에 존재하면 103번 자신이 지적한 "단일 진입점 부재"
문제를 이 세션이 반복하는 셈이다.

**이 세션의 제안**: `QUALITY_ELEVATION_ROADMAP.md` 상단에 "103번으로
대체됨(superseded) — 아래는 103번 작성 이전의 초기 판단이며 참고용으로만
보존" 주석을 달고, 실행 우선순위는 103번을 따른다. 다만
`QUALITY_ELEVATION_ROADMAP.md`에만 있고 103번에는 없는 두 항목은 남겨서
전달한다:
- Branch protection이 GitHub 요금제(private repo, free plan) 때문에
  막혀 있었다는 사실과, 사용자가 이를 보류하기로 결정했다는 기록
  (103번의 P0-05 "추적성" 항목과 관련은 있지만 GitHub 플랫폼 제약 자체는
  언급되지 않았다)
- DevEngine의 `zones.py` FROZEN 규칙이 실제로는 `.aios-zone`과 이미
  일치한다는 확인(공유 접점 문서 v1.3의 FROZEN-PAPER-ONLY 제안이 실제
  코드에는 반영된 적이 없음) — 향후 누군가 이 재분류를 다시 시도할 때
  참고할 것.

---

## 5. 이 세션이 별도로 확인한 것 — DevEngine 쪽 상태

- `ADR-2026-09-01-canonical-repository-designation.md`로 canonical
  저장소 3개(mihwa-aios/mihwa-aios-frontend/신규 GeonAhGim/mihwa-devengine)를
  공식 지정했다. Branch protection/CODEOWNERS 실 소유자 지정과 기존
  LIVE 코드 feature-gate 정책 owner는 명시적으로 보류했다(사용자 결정).
- DevEngine의 bare AIOS clone이 실제 origin보다 24개 커밋 뒤처져
  있었던 걸 오늘 동기화했다 — 이 문제 자체가 103번이 우려하는 "설계-코드
  추적성 부재"의 한 사례였다(DevEngine이 낡은 스냅샷으로 작업을 시작할
  뻔했다).
- DevEngine 로컬 저장소(이전엔 원격 없음)를 `GeonAhGim/mihwa-devengine`에
  새로 push했다 — `GeonAhGim/DevEngineProject`(별도 계보, merge-base
  없음)와는 무관.

---

## 6. 다음 행동 제안 (사용자 승인 필요, 이 세션이 임의 결정하지 않음)

1. ~~사용자가 Codex에게 신규 설계문서 생성 일시 중단을 명시적으로
   지시할지 결정.~~ → **해결됨** (§2 참고): Codex는 이미 자발적으로
   멈췄고, 재개 조건은 "Claude Code 구현 진척"이다. 남은 건 그 진척을
   어떻게 판단할지 — §2에 제안한 두 가지 재개 트리거(P0 항목 1개 종결,
   또는 HIGH 등급 red-team 항목 closed) 중 사용자가 택할지, 아니면
   다른 기준을 원하는지.
2. `QUALITY_ELEVATION_ROADMAP.md`에 supersession 주석 추가 여부 확정.
3. feature flag(임시)→물리적 격리(근본) 순서에 대한 Codex의 견해 확인
   후 103번 P0-02 조치 계획에 반영할지 결정 — 다만 Codex가 지금 반응할
   수 없는 상태이므로(§2), 이 질문은 재개 이후로 미뤄질 가능성이 높다.
