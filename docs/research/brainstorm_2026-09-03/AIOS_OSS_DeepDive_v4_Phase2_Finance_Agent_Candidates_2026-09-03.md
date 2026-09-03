# AIOS Phase 2(금융·에이전트 15종) 코드 레벨 검증 v4

작성: Fable | 2026-09-03 | [`AIOS_GitHub_Wide_Scan_Phase2`](AIOS_GitHub_Wide_Scan_Phase2_2026-09-03.docx)의
15개 후보 전부를 코드 레벨(CODE-VERIFIED)까지 검증. 원본 7개 리포트는
[`research_evidence_2026-09-03/`](research_evidence_2026-09-03/)에 있다.

## 결론 — 2차 광역조사의 등급표는 신뢰하지 말 것

Phase 2 문서 자체가 §6에서 "아이디어가 유사한 것과 프로덕션 품질이 검증된 것은 다르다"고
경고했는데, 코드를 열어보니 그 경고가 문서의 S+/S/A 등급 자체를 상당 부분 무효화한다. 15개
중 실제로 코드를 신뢰할 수 있는 것은 소수다.

| 등급 | 저장소 | 검증 결과 | 실제 상태 |
|---|---|---|---|
| S+ | parlali/trading-agent | **등급 그대로 유효** | LLM/실행 분리, 4벤뉴 어댑터, MCP, 3자 대사가 전부 코드로 확인됨. 단 1인 개발·스타 1개·CI 없음 |
| S+ | OnePunchMonk/AgentQuant | 부분 유효 | lookback/causal 가드는 진짜. walk-forward는 **죽은 코드**(존재하지 않는 모듈 import). 저장 게이트가 통과여부와 무관하게 무조건 실행(§2 참조) |
| S+ | eidostein/segnals-mcp | **등급 과대평가** | 커밋 1개, npm/Docker 이미지가 README에 광고돼 있지만 **존재하지 않음**. 확인(confirm) 게이트가 서버측 상태 없이 클라이언트 플래그만으로 우회 가능 |
| S | ulab-uiuc/coinjure | 부분 유효 | discovery→backtest→execution 모듈 분리는 진짜(7명, 100+ 커밋, 실제 UIUC 프로젝트). 승격 게이트는 `pnl>0` 단일 실행 체크뿐이고, 단일전략 경로는 그마저 건너뛰고 대화형 확인만 있음 |
| S | rock-876/autoquant | **등급 과대평가** | walk-forward/AST 안전검사는 진짜 코드. "제한된 파일만 수정"이라는 경계는 **CLAUDE.md 산문으로만** 존재 — 강제하는 코드 0건 |
| S | yogeshg665/quill-trading-agent | **등급 그대로 유효, 다만 소규모** | Risk Guardian이 완전히 분리된 결정론적 파이프라인 — AIOS 패턴을 정확히 재확인. 2.5개월 동면 상태 |
| S | longsizhuo/openInvest | **등급 상회** | 82스타·539커밋·94테스트·26 ADR, 실제 운영 사고 기록까지 있는 성숙한 프로젝트. 이번 배치에서 가장 신뢰할 만함 |
| A+ | Lumiwealth/lumibot | **등급 그대로 유효** | 11개 실브로커 백엔드, IOC/FOK까지 지원(LEAN보다 나음). 단 `setup.py`가 MIT라 주장하나 실제 LICENSE는 GPL-3.0(메타데이터 버그, 법적 리스크) |
| A+ | Lumiwealth/botspot-mcp | **검증 불가** | 소스코드 자체가 없음(5개 설정 파일, 4KB) — 비공개 호스티드 SaaS 커넥터일 뿐 |
| A+ | heyhaigh/trading-buddy | **등급 과대평가, 그러나 패턴은 유효** | 당일 생성·당일 방치, 6커밋. 그래도 해시체인 감사로그·정족수 기반 데이터 합의 패턴은 훔쳐올 가치 있음 |
| A | LLMQuant/Magents | **등급 과대평가** | "pod"는 프로세스 격리가 아니라 인메모리 객체 그룹. 자본배분은 등록순서 의존 버그가 있는 단순 균등분할 |
| A | TauricResearch/TradingAgents | **등급 상회** | 실제 논문 기반, 스타 102,351개, 최고 신뢰도. 단 순수 리서치/신호 프레임워크 — 실행/리스크는 전혀 안 건드림 |
| A | ryonzhang/mantle-quant | **완전히 실패한 프로젝트** | 해커톤 3일짜리, `npm run typecheck` 자체가 실패, provenance 구현이 다른 파일에서 존재하지 않는 함수를 import함(빌드 불가) |
| A | finnfujimura/agenttrader | **등급 과대평가** | 라이브 트레이딩 경로 자체가 없음(백테스트+페이퍼만). "Good? Yes/No" 게이트는 LLM 프롬프트 관례일 뿐 서버 강제 없음 |
| A | Erfaniaa/LLM-Auto-Trader | 부분 유효 | 리스크 분리는 진짜(CCXT 기반). "오토트레이더"보다는 과최적화 방지 백테스트 방법론(CPCV/PBO/DSR)이 진짜 자산 |

## 가장 중요한 발견 — "계산은 하되 막지는 않는다" 패턴이 5번째, 6번째로 재확인됨

[OSS Deep Dive v2](AIOS_OSS_DeepDive_v2_CodeLevel_CrossVerification_2026-09-03.md) §3.2가 AIOS의
`hard_fail_reasons`와 OBaI의 프롬프트 게이트를 "구조적으로 동일한 실패 양식"이라고 지적했는데,
이번 15개 검증에서 **같은 클래스의 결함이 최소 3건 더** 나왔다.

- **AgentQuant**: `reflect_node`가 `min_acceptable_sharpe` 미달을 확인하지만 그 결과는 재시도
  여부만 결정한다. `store_node`는 통과 여부와 무관하게 `best_result`를 무조건 저장한다.
- **autoquant**: "전략 파일만 수정 가능"이라는 경계가 `.claude/settings.json`도, git hook도,
  파일시스템 락도 없이 **CLAUDE.md 텍스트로만** 존재한다 — LLM 에이전트의 자발적 준수에
  100% 의존.
- **coinjure**: `engine promote --all`의 도움말은 "양(+)의 PnL 필터"라고 주장하지만 실제 코드는
  `lifecycle == 'paper_trading'` 상태만 확인하고 **PnL을 아예 검사하지 않는다**. 단일 전략
  경로(`--strategy-ref`)는 이 배치 게이트 자체를 건너뛰고 대화형 확인(`click.confirm(default=True)`)
  하나로 라이브 진입을 허용한다.
- **agenttrader**: README의 "Good? Yes/No" 승인 게이트가 서버 코드 어디에도 강제되지 않는
  LLM 프롬프트 관례일 뿐이다.

이제 이 실패 양식은 **AIOS 자신 + OBaI + AgentQuant + coinjure + agenttrader + autoquant(변형)** —
최소 6개 독립 시스템에서 확인됐다. 레지스트리 [I-07](AIOS_Registers_v1_Assumption_Contradiction_Invariant_Failure_2026-09-03.md)을
"주의할 만한 원칙"에서 **"업계 전반에 반복되는 검증된 안티패턴"**으로 신뢰도를 격상한다 — 이건
AIOS만의 문제가 아니라 "LLM 에이전트가 판정을 내리는 시스템"이라는 카테고리 전체의 구조적
함정이다.

## 새로 확인된 것

1. **Lumibot의 IOC/FOK 지원** — [LEAN 분석](research_evidence_2026-09-03/ext_lean.md)이 지적한
   "코어에 IOC/FOK가 없다"는 갭을, 같은 Python 생태계의 Lumibot은 갖고 있다. AIOS의 Execution
   Plane L4 설계 시 LEAN의 주문 무결성 패턴 + Lumibot의 TIF 지원을 함께 참고할 가치가 있다.
2. **라이선스 메타데이터는 실제 LICENSE 파일로 재확인해야 한다** — Lumibot의 `setup.py`가
   MIT라고 주장하지만 실제 `LICENSE`는 GPL-3.0이다. 앞으로 AIOS가 어떤 저장소든 의존성으로
   검토할 때 `setup.py`/`package.json`의 라이선스 필드가 아니라 `LICENSE` 파일 원문을 확인하는
   것을 절차로 못박는다.
3. **확인(confirmation) 게이트는 서버측 상태가 있어야 진짜다** — segnals-mcp의 "2단계 확인"이
   `confirm: true`라는 클라이언트 플래그 하나로 우회 가능했다(미리보기와 확인 호출을 잇는
   nonce/서버측 pending 상태가 없음). 이건 새로운 Invariant 후보다: **"확인이 필요한 작업은
   1회성 서버측 토큰으로 미리보기와 실행을 연결해야 하며, 반복 가능한 클라이언트 플래그만으로는
   불충분하다."** (레지스트리에 I-11로 추가 제안 — 세그널스 조사가 제안한 "I-09"는 이미
   레지스트리의 다른 항목과 충돌해 번호를 새로 부여한다.)
4. **openInvest의 실제 사고 사례가 AIOS 설계를 외부에서 검증** — 이미 앞 메시지에서 보고함
   (RiskEngine을 LLM 루프 밖에 두는 설계의 정당성).

## Quill/openInvest에 대한 재확인 — Policy Plane은 이미 옳다

이전 검토에서 "Quill이 AIOS 안 다뤄본 개념(독립 Risk Guardian)을 다룰까"가 재조사 사유였는데,
결론은 **"AIOS가 이미 하고 있는 것과 정확히 같은 패턴을 독립적으로 재발명했을 뿐, 새로
배울 것은 없다"**였다. 다만 이건 실패한 조사가 아니라 유효한 결과다 — AIOS의 Policy Plane
설계가 재발명이 아니라 업계에서 반복적으로 수렴하는 정답이라는 확증을 얻었다.

## 성숙도 사다리 갱신

15개 전부 최소 CODE-VERIFIED, 대부분 FAILURE-ANALYZED까지 도달했다. AIOS-MAPPED(실제 AIOS
설계에 구체적으로 반영)까지 간 것은:

- parlali/trading-agent → Execution Plane L4 설계 시 참고(다음 우선순위 문서에서)
- Lumibot → Execution Plane L4 설계 시 LEAN과 병행 참고
- Quill/openInvest → Policy Plane 설계 재확인(변경 없음)
- segnals-mcp → Agent Gateway Plane에 신규 Invariant 후보(I-11) 제안

나머지(coinjure, mantle-quant, autoquant, AgentQuant, Magents, TradingAgents, trading-buddy,
LLM-Auto-Trader, agenttrader, botspot-mcp)는 REJECTED 또는 "패턴 일부만 참고, 저장소 자체는
더 이상 추적 안 함" 상태로 레지스트리를 마감한다 — 이 15개에 대한 추가 조사는 계획하지 않는다.
