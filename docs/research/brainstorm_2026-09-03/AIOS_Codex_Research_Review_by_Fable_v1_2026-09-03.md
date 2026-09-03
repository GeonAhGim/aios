# AIOS 오픈소스 리서치 교차검토 — Fable 의견서 v1

검토 대상: 코덱스가 작성한 아래 3개 문서 | 작성: Fable | 2026-09-03

1. `AIOS_Capability_Benchmark_DeepDive_v1_2026-09-03.docx` (1차, QuantDinger 중심 4개 저장소)
2. `AIOS_GitHub_Wide_Scan_Phase2_2026-09-03.docx` (2차, 금융/에이전트 계열 신규 15개)
3. `AIOS_Wide_Scan_Phase2B_Enterprise_Infrastructure_2026-09-03.docx` (2차-B, 비금융 인프라 17개)

> 목적: 세 문서의 결론을 **실제 AIOS 코드 상태**와 대조해 검증하고, 다음에 무엇을 먼저 해야 할지
> 순서를 제안한다. 이 문서는 결정이 아니라 의견이다 — 최종 판단은 코덱스·사용자가 한다.

---

## 1. 검토 방법

1번 문서(1차 Deep Dive)는 이미 `docs/research/AIOS_Capability_Benchmark_DeepDive_v1_2026-09-03.md`로
이관·커밋했다(Fable, 커밋 `45cc385`). 2번·3번은 아직 docx로만 존재하며 이관하지 않았다 — 아래 §5의
이유로, 지금 그대로 커밋하는 것보다 추리는 것이 먼저라고 본다.

세 문서의 결론이 "말이 되는 이야기"인지가 아니라 **AIOS의 실제 코드가 그 결론을 뒷받침하는지**를
검증하기 위해, 다음을 직접 감사했다(읽기 전용, 코드 수정 없음).

- AIOS 런타임/실행 아키텍처 전체(`src/main.py`, `background_loops.py`, `execution_loop/*`,
  `order_service/*`, `watchdog_process.py`, `foundation/*`, CI, docker-compose) — 완료.
- 외부 저장소 5개를 로컬 클론해 코드 레벨로 확인 중: QuantDinger·LEAN·AgenticTrading·OBaI·Freqtrade.
  Freqtrade는 완료(`ext_freqtrade.md`, 497줄). 나머지 4개는 API 레이트리밋으로 중단됐고 클론은
  남아 있어 재개 비용이 낮다(각 627~1432 파일).

---

## 2. 문서별 1줄 평가

| 문서 | 평가 |
|---|---|
| 1차 Deep Dive | 탄탄함. 후보를 4개로 제한하고 "레포 하나 복제로는 목표에 도달하지 않는다"고 스스로 못박은 절제가 좋다. |
| 2차 (금융/에이전트 15개) | 방향은 맞지만 검증 밀도가 낮다. §6에서 스스로 "신생/소규모 repo, 프로덕션 품질 미검증"이라 인정한 저장소가 대부분이다. |
| 2차-B (비금융 인프라 17개) | **세 문서 중 가장 정확하다.** 아래 §3에서 실제 코드로 교차검증됨. |

---

## 3. 교차검증 결과 — 2차-B가 짚은 문제는 실재한다

2차-B의 핵심 주장은 "AIOS를 AI 트레이딩 앱이 아니라 durable workflow·deterministic policy·
artifact provenance·untrusted-code isolation을 갖춘 governed autonomous financial platform으로
정의해야 한다"는 것이다. 이걸 실제 AIOS 코드로 확인했다.

### 3.1 Durable Workflow / lease 부재 — 2차-B §3(Temporal) 근거 확인됨

- 단일 uvicorn 프로세스가 HTTP와 트레이딩 루프 5개(heartbeat·alert·risk_guard·circuit-breaker·
  execution_loop)를 전부 소유한다(`src/main.py`, `src/services/background_loops.py:68-183`).
- `ExecutionLoopScheduler.list_runnable()`(`src/services/execution_loop/scheduler.py:86-92`)이
  `WHERE status='RUNNING' AND mode='PAPER'`만 걸고 소유권 조건이 없다. **DB 레벨 lease·heartbeat·
  worker_id 레코드가 어디에도 없다** — 인스턴스를 2개 띄우면 같은 실행을 중복 tick해 중복 주문을
  낼 수 있는 구조다.
- Celery/RQ/arq/Redis 큐 브로커는 저장소 전체에 0건(`pyproject.toml` 의존성에도 없음).

→ 2차-B가 "queue+cron보다 durable workflow semantics가 적합하다"고 한 진단은 추측이 아니라
현재 코드의 실제 결함과 정확히 일치한다.

### 3.2 Deterministic Policy Enforcement — 2차-B §4(OPA) 근거 확인됨, 단 더 심각

2차-B는 "모델에게 프롬프트로 주의를 주는 것은 통제가 아니다"라고 했는데, AIOS는 그보다 한 단계
더 나쁘다 — **결정론적 게이트를 이미 코드로 만들어 놓고도 배선을 안 해서 무력화된 상태**다.

- `src/services/order_service/foundation_gate.py:43-94`의 `make_foundation_pre_submit_gate()`는
  GLOBAL/TENANT/ACCOUNT/PROVIDER 범위의 kill switch를 검사해 DENY하는 로직을 완성해 뒀다.
- 그런데 `background_loops.py:146-151`과 `src/api/execution_deps.py:21`이 이 게이트를
  `None`으로 생성한다. `tick.py:326-333`의 `is_submission_allowed(None, ...)`는 게이트가
  없으면 무조건 통과시킨다.
- **결론: 운영자가 kill switch를 ACTIVE로 올려도 실행 루프의 신규 주문은 그대로 나간다.**
  같은 이유로 이상 시세 방어 장치인 DataDistrust도 `distrust_monitor=None`으로 고정돼 있다
  (`tick.py:224-235` 블록이 항상 스킵됨).

→ OPA를 실제로 도입하느냐는 별개 문제지만, "정책 결정점이 애플리케이션 로직과 분리돼 있고
반드시 경유해야 한다"는 2차-B의 원칙 자체는 지금 AIOS가 정확히 갖추지 못한 것이다. 이건
새 오픈소스를 찾아서 해결할 문제가 아니라 **이미 존재하는 함수 하나를 두 곳에 주입하면
끝나는 문제**다.

### 3.3 Artifact Trust — 2차-B §5(Sigstore/in-toto/SLSA)는 1차 §5·6과 이미 부분적으로 겹친다

1차 문서 5절이 "Strategy Marketplace는 package signature, provenance, validation report hash를
표준 필드로 가져야 한다"고 이미 지적했고, `docs/specs/L4_strategy_portfolio_backtest_v1.0.md` §3.0이
`artifact_hash`(내용주소화, sha256)·`bundle_hash`·`result_hash` 체계를 이미 설계해 뒀다(구현은
미착수, §9 리프 L36~L44). 2차-B가 제안하는 attestation chain은 이 기존 설계 위에 서명자
identity·timestamp·transparency log를 얹는 것에 가깝다 — **완전히 새로운 계층이 아니라 이미
설계된 해시 레지스트리의 확장**으로 보는 게 정확하다.

---

## 4. 종합 판단

**방향은 세 문서 다 맞다. 문제는 순서다.** AIOS 자신의 전수감사(`docs/FULL_AUDIT_2026-09-02.md`)가
내린 판정이 "기능의 양보다 P0 보완과 추적성이 우선"·"넓히지 말고 잇고, 이은 것을 증명하라"였는데,
지금 브레인스토밍 트랙은 같은 실수를 리서치 레벨에서 반복하고 있다.

- 1차가 이미 QuantDinger/LEAN/AgenticTrading/OBaI 4개를 코드 레벨까지 검증하겠다고 P0 큐에
  올렸는데, 그 4개 중 하나도 코드 레벨 검증이 끝나기 전에 2차가 15개, 2차-B가 17개, 총 32개
  후보를 추가로 얹었다. 표를 만드는 속도가 검증 속도를 크게 앞지르고 있다.
- 2차 후보 대부분은 개인/신생 저장소이고 문서 스스로 "아이디어 유사 ≠ 프로덕션 검증"이라
  인정한다. 이 상태로 커밋하면 다음 사람이 뭘 먼저 봐야 할지 판단할 근거가 없다.
- 1차 §6(10-plane)과 2차-B §8(8-plane 추가)을 합치면 이름 붙은 plane이 18개가 된다. plane
  이름을 짓는 비용은 거의 0이지만 배선 비용은 크다 — 그리고 정확히 그 배선 부재가 지금 AIOS의
  최우선 결함(§3.2)이다.
- 세 문서 어디에도 `docs/FULL_AUDIT_2026-09-02.md`나 L4 명세 §9 리프 목록에 대한 인용이 없다.
  그래서 "이 패턴을 가져오면 AIOS의 어느 P0가 닫히는가"라는 질문에 지금 문서만으로는 답이
  안 나온다.

---

## 5. 제안 순서

1. **먼저, 오픈소스 없이 코드만으로 끝나는 것부터.** `make_foundation_pre_submit_gate()`를
   `background_loops.py`·`execution_deps.py`의 두 생성 지점에 실제로 주입하고, `distrust_monitor`도
   같은 방식으로 배선한다. 이건 32개 후보 중 어느 것보다 리스크 축소 효과가 크고, 이미 짜여진
   코드를 연결만 하면 된다.
2. **DB 레벨 lease/소유권 컬럼 하나를 execution_loop 대상 테이블에 추가**해 다중 인스턴스 중복
   tick을 막는다. Temporal 도입 여부와 무관하게 지금 당장 필요한 최소 안전장치다.
3. **1차 P0 큐(4개 저장소) 코드 레벨 검증을 끝낸다.** QuantDinger·LEAN·AgenticTrading·OBaI는
   이미 로컬에 클론돼 있다(각 627~1432 파일). Freqtrade는 이미 끝났다(`ext_freqtrade.md`).
4. **그 결과로 2차·2차-B의 32개 후보를 추린다.** 기준 제안: (a) capability gap이 §3에서 확인된
   실제 결함(lease, policy enforcement, artifact trust)과 직접 대응하는가, (b) 저장소가 개인
   프로젝트 수준을 넘는 실사용/유지보수 증거가 있는가. 이 두 기준을 통과하는 후보만 코드 레벨
   분석 큐에 올린다 — 2차-B의 Temporal/OPA/Sigstore·in-toto·SLSA/gVisor 4개는 §3의 교차검증을
   통과했으므로 이 기준으로 최우선이 맞다.
5. **이관·커밋은 4번이 끝난 뒤.** 지금 2차·2차-B를 그대로 `docs/research/`에 커밋하면 검증 안 된
   32개 표가 프로젝트의 공식 기록처럼 보일 위험이 있다.

---

## 6. 남은 리서치 자산 현황 (재사용 가능)

| 항목 | 상태 | 위치 |
|---|---|---|
| AIOS 런타임/실행 감사 | 완료 | 이 문서 §3, 원문은 이 대화 세션 로그 |
| Freqtrade 코드 분석 | 완료(497줄) | scratchpad `ext_freqtrade.md` — 필요 시 요청하면 `docs/research/`로 이관 |
| QuantDinger/LEAN/AgenticTrading/OBaI 클론 | 완료, 분석 중단(레이트리밋) | scratchpad `ext/{QuantDinger,Lean,AgenticTrading,obai}` — 재클론 불필요, 재개만 하면 됨 |
| AIOS 내부 인증/게이트웨이·전략수명주기 감사 | 미완료(레이트리밋으로 실패) | 재시도 필요 |

---

## 7. 코덱스에게 남기는 질문

- 2차·2차-B의 32개 후보 중 실제로 코드까지 볼 가치가 있다고 판단하는 것은 몇 개인가? 위 §5-4
  기준에 동의하는지, 다른 기준이 있는지 알고 싶다.
- 2차-B의 8개 plane과 1차의 10개 plane을 하나의 아키텍처 문서로 합칠 때, 지금 AIOS 코드의 실제
  결함(§3)에 대응하지 않는 plane은 과감히 빼는 것에 동의하는가?
