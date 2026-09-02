# ADR-2026-08-29-E: FROZEN Zone 부분 개방 — PAPER 실행 전용 한정

## Status
**Accepted (2026-08-29)** — 15.6-A/15.6-D를 대체하지 않고 **부분 완화**한다.
FROZEN Zone은 여전히 존재하되, "완전 봉쇄" 대신 "PAPER 실행 전용으로
개방, LIVE는 별도 종료조건 충족까지 계속 봉쇄"라는 중간 분류
(**FROZEN-PAPER-ONLY**)를 신설한다.

## Context

사용자가 초도 프로젝트(MiHwa, 개발로드맵.txt/DEVELOP_V1R1.MD)부터 지금
AIOS까지의 계보를 재검토한 뒤, 단순 MVP를 넘어 엔터프라이즈급 기능·
서비스(고급 매매 기능, 다자산군 파생상품 포함)로 확장하기로 결정했다.
이 확장은 FD-8(Strategy/Portfolio/Risk/Executor)의 실동작 구현을
요구하는데, 이 경로는 공유접점문서 §1.2(15.6-A)에 의해 **"15.6-D
종료조건 충족 전까지 어떤 PR도 대상 불가 — 사람이 작성해도 동일"**로
못박혀 있다.

15.6-D 종료조건 3가지 중:
1. 8.2-A Master Authority 구현 + 회귀테스트 — 미충족(이 ADR이 여는 작업
   자체가 이 조건을 채우는 과정)
2. 4.9 Human Approval 보안요건(MFA·이중승인) **실제 운영 계정 적용
   완료** — 구조적으로 미충족 가능(Bitget/KIS 실키 자체가 아직 없음,
   `.env` 비어있음). 실키가 없으면 "실제 운영 계정"이 존재하지 않아
   이 조건은 원천적으로 충족 불가능하다.
3. 3.4/9.10 자율성 Level 3 Hard Gate — 사실상 충족: 자동화 수준 추적
   시스템 자체는 없지만(`execution_service.py` 자체 docstring 참조),
   현재 구현은 이를 **더 보수적인 방향으로 과잉충족**한다 — LIVE 모드는
   자동화 수준과 무관하게 항상 FD-10.1 승인을 요구하도록 이미 배선돼
   있다(과소 안전장치보다 과잉 승인 요구가 안전하다는 원칙).

즉 조건 2만이 실제 장애물이며, 이는 "더 열심히 구현"으로 해결되는
문제가 아니라 **실계정이 없으면 구조적으로 불가능한 조건**이다. 이
ADR은 조건 2가 채워지기 전까지 LIVE 경로만 코드 레벨로 계속 봉쇄하고,
조건 1(Master Authority 구현)과 조건 3(이미 충족)이 실제로 요구하는
PAPER 모드 판단·실행 로직은 지금 개방한다.

## Decision

### ADR-2026-08-10-D §① 5개 질문 게이트 적용 결과

| # | 질문 | 판정 |
|---|---|---|
| 1 | 사용자 자금 커스터디를 새로 발생시키지 않는가? | **예** — PAPER 실행은 가상 체결이라 실자금 이동 없음 |
| 2 | Master Authority/Kill Switch 우선순위를 약화시키지 않는가? | 예 — 아래 "설계 제약" 항목으로 보장 |
| 3 | 자기검증 금지 원칙(9.9)을 위반하지 않는가? | 예 — ADR-2026-08-10-D가 이미 1인 체제 보완장치(5문항+180초 숙고)를 마련해둠, 이 결정 자체가 그 절차를 따르는 중 |
| 4 | FROZEN Zone을 15.6-D 종료조건 충족 전에 개방하지 않는가? | **아니오** |
| 5 | 되돌릴 수 있는가? | 예 — 정책/코드 변경, PAPER 한정이라 실피해 없음, LIVE는 계속 봉쇄돼 있어 원상복구도 용이 |

4번이 "아니오"이므로 ADR-2026-08-10-D §① Step 3(경고+사전동의) 대상.
이 세션 대화 중 5개 질문 판정 전체를 사용자에게 명시적으로 제시했고,
사용자가 "PAPER 한정 ADR로 진행"을 명시적으로 재확인함으로써 사전동의
절차를 충족했다(Step 3 완료 근거).

### 개방 범위 — FROZEN-PAPER-ONLY

**개방되는 것**:
- `src/core/strategy/`, `src/core/portfolio/`, `src/core/risk/`,
  `src/core/executor/`에 실제 판단 로직 PR 가능(사람이 작성해도 15.6-A
  예외 적용 — 이 ADR이 그 예외 자체다)
- `StrategyEngine.evaluate()` / `PortfolioEngine.allocate()` /
  `RiskEngine.check()` / `Executor.execute()`의 03번 §3.5~3.8 시그니처를
  그대로 채운다 — 인터페이스 자체는 변경하지 않는다(공유접점문서 §2
  동결 계약과 무관, §2는 AIOSTask/FSMStrategyConfig/Order State
  Machine/Exchange Adapter만 다룸)
- FD-4(주문 전송 계층, 원래 SCAFFOLD였으나 미구현 상태로 방치돼 있던
  것 확인됨)도 이 참에 함께 구현 — Executor가 호출할 대상이 없으면
  FROZEN 개방 자체가 무의미

**계속 봉쇄되는 것 (코드 레벨 강제)**:
- LIVE 모드 주문 실행. `Executor.execute()`(및 이를 호출하는 모든
  경로)는 대상 실행의 `mode != 'PAPER'`이면 **무조건**
  `FrozenZoneLiveModeBlockedError`를 던진다 — 정책 문서 상의 금지가
  아니라 실행되는 코드 자체의 하드 가드다. 이 가드를 제거하려면
  15.6-D 조건 2(실계정 MFA·이중승인 운영 적용)가 실제로 충족된 뒤
  **별도 ADR**로 명시적으로 해제해야 한다 — 이 ADR은 그 해제를
  포함하지 않는다.
- `aios/kernel/policy/`, `aios/kernel/permission/`(DevEngine 거버넌스
  경로)은 이 ADR의 대상이 아니다 — AIOS 트레이딩 판단 로직만 다룬다.

### 설계 제약 (질문 2 보장 근거)

FD-8 구현 시 반드시 지켜야 하는 것 — 이후 실제 구현 leaf들이 이 제약을
위반하면 그 leaf는 이 ADR의 승인 범위를 벗어난다:

1. **RiskEngine은 결정론적 규칙만** — `risk_policy.yaml`의 8.2-B 8개
   지표(Daily Loss, MDD, 레버리지, 집중도, 전략배분, VaR, 상관관계,
   거래빈도)를 코드로 그대로 평가한다. 어떤 LLM 호출·Agent 판단도
   개입하지 않는다(8.2-A Master Authority 원칙 — 이 세션 전체에서
   반복된 "판단 로직은 결정론적이어야 한다" 원칙의 FROZEN 버전).
2. **8.6-B Kill Switch 우선순위 유지** — Watchdog > Human > Circuit
   Breaker > (Debate, 미구현) 순서. RiskEngine.check()는 Watchdog가
   이미 PAUSED로 전환한 실행에 대해 항상 거부해야 하며, 이 순위를
   우회하는 경로를 만들지 않는다.
3. **Executor는 판단하지 않는다** — 03번 §3.8 그대로, 승인된
   AllocationDecision+RiskCheckResult만 받아 FD-4.2를 호출한다.
   `risk_result.approved=False`인 건이 Executor에 도달하면 그 자체가
   상위 로직 버그다(방어적 assert 유지).
4. **다자산군(코인 선물/옵션) 확장은 별도 leaf** — 이 ADR은 FROZEN 개방
   자체를 다루고, 자산군별 리스크 지표(레버리지 마진콜, 청산가, 만기,
   그릭스)는 후속 스펙 문서(신규 FD 섹션 또는 06번 §6.1-A 개정)로 먼저
   확정한 뒤 구현한다 — "스펙 먼저, 리프 단위 구현"이라는 이 프로젝트의
   기존 방법론을 다자산군 확장에도 동일 적용.

## Rejected Alternatives

- **LIVE까지 포함해 완전 개방**: 15.6-D 조건 2(실계정 MFA·이중승인)가
  실키 부재로 구조적으로 충족 불가능한 상태에서 LIVE를 열면 검증되지
  않은 실매매 판단이 실제 자금에 그대로 노출된다 — 기각.
- **스펙 문서만 쓰고 코드는 그대로 봉쇄 유지**: 사용자가 명시적으로
  "판단엔진+PAPER 실행까지" 착수를 요청 — PAPER는 질문 1(자금 커스터디
  없음)을 통과하므로 완전 봉쇄를 유지할 이유가 없다고 판단 — 기각.
- **자동화 수준 추적 시스템을 먼저 완성한 뒤 개방**(조건 3 문자 그대로
  충족): 현재의 "LIVE는 항상 승인 필요" 보수적 폴백이 이미 Level 3
  Hard Gate의 취지(고위험 트리거는 항상 인간 승인)를 초과 충족하므로,
  추적 시스템 자체를 새로 만드는 건 이 ADR의 목적(FROZEN 개방)에 비해
  과잉 선행조건 — 기각.

## Impact

- **15.6-A 재분류**: `src/core/strategy/`, `portfolio/`, `risk/`,
  `executor/`가 FROZEN → **FROZEN-PAPER-ONLY**로 변경. `aios/kernel/
  policy/`, `permission/`은 FROZEN 유지(변경 없음).
- **공유접점문서(v1.2 → v1.3) 갱신 필요** — §1.2 Zone 표, §1.4 종료조건
  섹션에 이 ADR 참조 추가. **이 문서는 claude.ai의 "미화프로젝트-AIOS"와
  "미화프로젝트-DevEngine" 양쪽 지식저장소에 각각 수동 재업로드해야
  한다** — claude.ai Projects는 프로젝트 간 자동 참조를 지원하지 않으므로
  (공유접점문서 자체의 원칙), 이 파일 갱신만으로는 DevEngine 프로젝트
  세션이 이 변경을 알 수 없다.
- **CODEOWNERS**: FROZEN 경로의 소유자 지정(10번 문서 v1.6 §1.4)이
  실제로 존재한다면, FROZEN-PAPER-ONLY 재분류에 맞춰 갱신 필요 — 이
  세션에서 CODEOWNERS 실물 파일 존재 여부는 별도 확인 필요(후속 작업).
- **06_mvp_scope_v1.3.md §6.4 "명시적 제외" 갱신 필요** — "Strategy/
  Portfolio/Risk/Executor의 실제 판단 로직(FROZEN, 정책문서 15.6-D
  이후)" 문구를 "PAPER 한정 개방(이 ADR), LIVE는 계속 제외"로 정정.
- **03_core_modules_v1.1.md §3.5~3.8 STATUS 갱신** — `FROZEN-INTERFACE-
  ONLY` 주석을 `FROZEN-PAPER-ONLY — LIVE 경로는 ADR-2026-08-29-E 참조`
  로 정정, `NotImplementedError` 제거 대상임을 명시.

## References
- 공유접점문서 v1.2 §1.2(15.6-A Zone 분류), §1.4(15.6-D 종료조건), §2(동결 계약 — 이 ADR과 무관함을 확인)
- ADR-2026-08-10-D(플랫폼 레벨 승인 게이트, 이 ADR이 따른 절차의 근거)
- 03_core_modules_v1.1.md §3.5~3.9(FROZEN 인터페이스 시그니처)
- 기능설계문서_v1.21.md FD-4(주문 전송, 미구현 확인됨), FD-8(판단 계층), FD-9(안전장치, 8.6-B/9.9)
- 06_mvp_scope_v1.3.md §6.3(Definition of Done), §6.4(명시적 제외)
- risk_policy.yaml(8.2-B 8개 지표 Draft 수치)
