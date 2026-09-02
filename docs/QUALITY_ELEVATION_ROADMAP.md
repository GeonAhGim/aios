# AIOS Quality Elevation Roadmap

> 이 문서는 `RED_TEAM_FINDINGS.md`와 짝을 이루는 별도 문서다. 레드팀 문서가
> "구체적으로 재현 가능한 버그"만 기록하는 반면, 이 문서는 **엔터프라이즈
> 청사진(`GeonAhGim/AIOSproject`의 18~32번 문서)과 지금 실제 코드/거버넌스
> 상태 사이의 간극을 메우기 위한 브레인스토밍·우선순위·실행 로드맵**이다.
>
> 작성 경위: DevEngine 세션(별도 Claude Code 세션, `C:\devengine\mihwa-devengine`
> 작업 중)이 `GeonAhGim/AIOSproject`의 18~32번 엔터프라이즈 설계 문서 전체와
> Codex가 남긴 30/32번 문서의 최신 증거 기록을 읽고, 사용자와의 대화에서
> 정리한 내용을 이 문서로 옮겼다. **이 세션은 AIOS 코드를 직접 수정하지
> 않는다** — `RED_TEAM_FINDINGS.md`와 동일한 원칙. AIOS 구현을 맡을 (다른)
> Claude Code 세션 또는 Codex가 이 문서를 실행 계획으로 사용하면 된다.
>
> 기준일: 2026-09-01

---

> **🟡 SUPERSEDED (2026-09-02)** — 이 문서 작성 이후 `AIOSproject`의
> `103_enterprise_architecture_full_audit_and_remediation_brief_v1.0.md`가
> 훨씬 더 정밀한 버전으로 나왔다(P0 4개+P1 4개+P2 5개, 완료조건·release
> gate 포함, 33~102번 전체를 감사한 결과). **실행 우선순위는 이제 103번을
> 따른다** — 이 문서는 103번 작성 이전의 초기 판단이며 참고용으로만
> 보존한다(제안: `104_claude_code_review_and_open_questions_v1.0.md` §4).
>
> 다만 103번에는 없고 이 문서에만 있는 두 가지는 여전히 유효하니 참고할 것:
> 1. **Branch protection이 GitHub 요금제(private repo, free plan) 때문에
>    API로 켜지지 않는다는 사실**과, 사용자가 이를 보류하기로 결정했다는
>    기록(103번의 P0-05 "추적성" 항목과는 관련 있지만, 이 GitHub 플랫폼
>    제약 자체는 103번에 언급되지 않았다).
> 2. **DevEngine의 `zones.py` FROZEN 규칙이 실제로는 `.aios-zone`과 이미
>    일치한다는 확인** — 공유 접점 문서 v1.3의 FROZEN-PAPER-ONLY 재분류
>    제안이 실제 코드에는 반영된 적이 없다는 것. 향후 누군가 이 재분류를
>    다시 시도할 때 참고.

---

## 왜 이 문서가 필요한가

`31_enterprise_architecture_coverage_audit_v1.0.md`가 스스로 내린 판정은
"paper-only 구현 착수는 가능하나, 엔터프라이즈 출시 자격은 아직 없다"다.
이 문서는 그 간극을, "설계를 더 정교하게 다듬는 것"이 아니라 **이미 진단된
문제를 닫고, 선언만 돼 있는 통제를 실제로 작동시키는 것**으로 메우는
실행 순서를 제시한다. 각 항목은 우선순위, 근거, 구체적 행동, 예상 비용
순으로 적는다.

---

## 우선순위 1 — 이미 진단된 실제 버그부터 닫는다

`RED_TEAM_FINDINGS.md`의 2026-08-29-06~18번 항목(총 10건)이 아직 미해결
상태다(이 문서 작성 시점 기준 `git status`에 여전히 uncommitted). 이 중
엔터프라이즈 청사진 자신의 원칙과 직접 충돌하는 것부터 처리 순서를
매기면:

1. **2026-08-29-08 (execution_service Kill Switch 우회)** — `27_security_
   reliability_and_operational_governance_v1.0.md` §3이 "Kill Switch는
   execution plane에서 독립적으로 강제되고, 통제면 장애에도 안전 상태로
   갈 수 있어야 한다"고 명시한 바로 그 원칙이 코드에서 깨져 있다. 새 기능을
   더 쌓기 전에 최우선으로 닫아야 한다.
2. **2026-08-29-10 (SecretBundle.model_dump() 평문 유출)** — 27번 문서
   §1이 "secrets는... 애플리케이션 로그·LLM context·소스코드에 기록하지
   않는다"를 최우선 보호 대상으로 명시. `SecretStr` 타입 전환은 半나절
   이내 작업이며 파급 범위가 좁다(FastAPI가 이 객체를 직렬화하는 경로가
   생기기 전에 막는 예방적 수정).
3. **2026-08-29-11 (MFA 영구 비활성화)** — 25번 문서(User Trust)가
   요구하는 "사용자가 항상 자신의 권한/보안 상태를 이해·통제할 수 있어야
   한다"는 약속과 정면 충돌. 재현 조건이 간단해(토큰 탈취+틀린 코드 1회)
   실제 악용 난이도가 낮다.
4. 나머지 7건(마켓플레이스 자기승인, portfolio race, TOTP replay, 로그인
   타이밍, risk_policy 검증 부재, EventBus audit_sink crash, 반려사유
   유실, strategy_builder dormant 버그, 거래소 어댑터 2건)은 순서 상관없이
   진행 가능 — 개별 파일 범위가 좁아 서로 의존성이 없다.

**행동**: 이 로드맵을 실행하는 세션은 먼저 `RED_TEAM_FINDINGS.md`를 열어
10건을 하나씩 처리하고, 각 항목 상태를 FIXED로 갱신하며 커밋 메시지에
`docs/RED_TEAM_FINDINGS.md#2026-08-29-NN` 형식으로 참조를 남긴다(이미
5~52번 항목에서 확립된 관례).

---

## 우선순위 2 — 선언뿐인 통제를 실제 작동으로 바꾼다

### 2.1 Branch protection / CODEOWNERS — 사용자 결정으로 보류(2026-09-01)

GitHub API가 `mihwa-aios`에 branch protection을 걸려는 시도에
`"Upgrade to GitHub Pro or make this repository public to enable this
feature"`(403)를 반환한다 — private repo에서 이 기능 자체가 막혀
있다. 사용자가 GitHub Pro 구독을 시도했으나 세 차례 재확인(`GET /user`의
`plan.name`)에서도 계속 `"free"`로 남아있었다(GitHub Copilot 등 계정
플랜과 무관한 별도 상품을 구독했을 가능성이 유력) — **사용자가 이
경로를 더 기다리지 않고 명시적으로 보류(제외)하기로 결정했다.**

**따라서 이 로드맵을 실행하는 세션은 branch protection/CODEOWNERS 하드
enforcement를 전제로 작업 계획을 짜면 안 된다.** GitHub 쪽 merge gate는
당분간 없는 것으로 간주하고, 아래 2.2(DevEngine 파이프라인을 실질적
게이트로)를 유일한 실제 방어선으로 취급한다. 나중에 사용자가 실제로
GitHub Pro/Team을 확보하면(https://github.com/settings/billing/plans)
그때 아래 설정을 다시 시도한다 — 그 전까지 이 하위 항목은 실행 순서에서
제외한다.

<details>
<summary>참고용 — Pro/Team 확보 후 실행할 설정(지금은 실행하지 않음)</summary>

```bash
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/user | jq .plan
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
  https://api.github.com/repos/GeonAhGim/mihwa-aios/branches/main/protection
```

plan이 `pro`/`team`으로 바뀌어 있고 두 번째 호출이 403이 아니면, 아래
설정으로 `main`에 branch protection을 건다:
- require pull request before merging (최소 승인 1인 이상 — 승인자가
  정해지지 않았다면 사용자 본인 1인으로 시작)
- require status checks to pass (`ci: add portable quality gate` 워크플로,
  DevEngine 쪽과 동일하게 ruff/mypy/pytest를 필수 체크로)
- CODEOWNERS의 `@{owner}` placeholder를 실제 GitHub 사용자명으로 교체

</details>

지금은(2026-09-01, 사용자 결정) 이 항목을 실행하지 않고 우선순위 2.2로
바로 넘어간다.

### 2.2 DevEngine 파이프라인을 실질적 게이트로 확실히 만든다

GitHub이 merge gate를 강제 못 하는 동안, 실제로 작동하는 유일한 통제는
DevEngine의 Consensus+사람승인 파이프라인이다. 확인할 것:

- DevEngine의 `.env`에서 `AIOS_GITHUB_REPO`가 정확히 `GeonAhGim/mihwa-aios`
  를 가리키는지 (ADR-2026-09-01로 이 저장소가 공식 canonical로 지정됨)
- DevEngine의 bare clone(`AIOS_BARE_REPO_PATH`)이 origin과 동기화돼
  있는지 — 오래된 스냅샷에서 작업을 시작하면 Consensus가 검토하는 diff가
  실제 최신 코드와 어긋난다
- Capability Token의 FROZEN_PATTERNS(`src/capability/zones.py`)이
  ADR-2026-08-29-E(FROZEN-PAPER-ONLY 부분 개방)를 실제로 반영하고
  있는지 — 아래 우선순위 6 참조

---

## 우선순위 3 — 기존 LIVE 코드를 feature flag로 격리한다

`32_repository_boundary_reconciliation_v1.0.md` §3(High-risk boundary
finding)이 반복 지적하는 문제: `mihwa-aios`에 이미 암호화 자격증명,
PAPER→LIVE 전환, 마켓플레이스 지갑 코드가 있는데, 청사진(19번 §6)은
"P0/P1은 paper 전용"이라고 못박고 있다. 기존 코드를 지우거나 비활성화
하면 진행 중인 기능을 손상시킬 위험이 있으므로, 다음을 권장한다.

**행동**: 환경변수 기반 feature flag(예: `AIOS_LIVE_EXECUTION_ENABLED`,
기본값 `false`)를 만들어, LIVE 모드로의 실제 전이가 일어나는 지점
(`convert_to_live()`류 함수, LIVE 주문 제출 경로)에서 이 플래그를
확인하고 꺼져 있으면 명시적 오류로 거부한다. 이러면:
- 코드/테스트/이력은 그대로 보존된다
- 런타임 동작이 청사진의 "P0/P1 = paper only" 약속과 즉시 일치한다
- 실제 LIVE 오픈 결정이 나면(29번 문서 §4의 decision register 항목들이
  승인된 뒤) 플래그 하나만 켜면 된다

`32_repository_boundary_reconciliation_v1.0.md` §4의 결정 #5(누가 이
feature-gate 정책의 owner인지)는 여전히 사람이 정할 문제이지만, 이
플래그 자체를 만들어두는 건 그 결정을 선점하지 않는 순수 방어적
엔지니어링 작업이라 지금 진행해도 안전하다.

---

## 우선순위 4 — 계약을 "존재"에서 "실제 배선"으로

`30_implementation_readiness_evidence_v1.0.md` §3.1이 스스로 인정하듯,
`PolicyDecision`/`OrderIntent` 등은 스키마와 fixture, 그리고 PR #5의
`project_paper_eligible_strategy()`(PAPER_TRADING → PAPER_ELIGIBLE
투영, 실행/자격증명/결제 의존성 없음)까지는 있지만, **실제로 라우팅
가능한 producer/consumer 경로, tenant별 evidence 영속화, 사용자 대면
control-center 여정은 아직 없다.**

이건 Codex가 스스로 정의한 다음 실행 목표(`29_enterprise_readiness_
program_v1.0.md`의 M-1)이고, 우선순위 1~3을 마친 뒤의 본 작업이다.
`30_implementation_readiness_evidence_v1.0.md` §3의 8개 최소 모듈
(`identity-access`, `policy-decision`, `strategy-csm`, `strategy-
validation`, `strategy-registry`, `paper-deployment`, `audit-evidence`,
`control-center-bff`) 순서를 그대로 따르면 된다.

---

## 우선순위 5 — 새 세션이 이 맥락을 놓치지 않게 한다

DevEngine 세션조차 `GeonAhGim/AIOSproject`의 18~32번 문서 존재를
오늘 우연히(Codex 세션 로그를 추적하다가) 발견했다. `mihwa-aios`나
`mihwa-devengine` 저장소를 직접 여는 세션은 이 문서 저장소 자체를
모를 가능성이 실제로 높다.

**행동**: `mihwa-aios`와 `mihwa-devengine` 저장소 루트에 아주 짧은
포인터 파일을 추가한다(예: `ARCHITECTURE.md`):

```markdown
# Architecture

작업을 시작하기 전에 반드시 `GeonAhGim/AIOSproject`(private)를 먼저
확인하세요 — 특히 21번 문서(에이전트 운영 프롬프트)와 29번 문서
(결정 등록부, 에이전트가 임의로 정할 수 없는 7가지 항목)를 먼저 읽고
시작해야 합니다. `docs/RED_TEAM_FINDINGS.md`와
`docs/QUALITY_ELEVATION_ROADMAP.md`도 함께 확인하세요.
```

---

## 우선순위 6 — DevEngine의 FROZEN 규칙 동기화 (정정: 실제로는 문제 없음, 2026-09-01 재확인)

이전 버전의 이 문서는 "AIOS는 `ADR-2026-08-29-E`로 FROZEN-PAPER-ONLY를
실제 반영했는데 DevEngine의 `zones.py`만 구버전"이라고 적었으나, 이는
**AIOS↔DevEngine 공유 접점 문서 v1.3(제안 문서)만 보고 판단한 오류였다.**
2026-09-01에 AIOS의 실제 `.aios-zone` 파일을 origin/main 최신 상태에서
직접 확인한 결과, FROZEN-PAPER-ONLY 재분류는 **실제 강제 파일에는 한
번도 반영된 적이 없다** — `.aios-zone`은 여전히 `src/core/strategy/`,
`portfolio/`, `risk/decision/`, `executor/`를 100% FROZEN으로 명시한다.
즉 DevEngine의 `zones.py`는 이미 AIOS의 실제 경계와 정확히 일치한다.

**행동**: 아무것도 하지 않는다. `zones.py`를 완화하는 방향으로 수정하면
오히려 DevEngine을 AIOS 자신의 현재 실제 경계보다 더 느슨하게 만드는
역효과가 난다. 만약 나중에 `.aios-zone` 자체가 실제로 갱신돼
FROZEN-PAPER-ONLY 등급이 파일에 반영되면, 그때 `zones.py`를 그에 맞춰
갱신한다 — 이 문서 갱신 시점 기준으로는 그 갱신이 아직 일어나지
않았다.

---

## 실행하지 말아야 할 것 (참고)

`29_enterprise_readiness_program_v1.0.md` §4의 decision register 7개
항목(실거래 최초 범위·법인/관할국가·결제모델·Master 인원수·클라우드
벤더·집단학습 동의정책·공개출시 고지)은 이 로드맵의 어떤 우선순위로도
대신 결정하지 않는다. 이 항목들이 필요해지는 지점(우선순위 3의 feature
flag를 실제로 켜는 시점, 또는 M-3/M-4 게이트)에 도달하면 진행을 멈추고
사용자 승인을 받는다.
