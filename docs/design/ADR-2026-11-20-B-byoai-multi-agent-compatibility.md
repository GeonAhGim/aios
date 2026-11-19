# ADR-2026-11-20-B: BYOAI 커넥터 보강 — 사용자가 쓰는 어떤 AI 에이전트(Claude·ChatGPT·Gemini·Copilot·로컬 LLM)도 붙는 구조

## Status
Accepted (2026-11-20, 사용자 요청: "사용자들이 어떤 AI 에이전트를 쓸지 몰라도 Claude·ChatGPT·Gemini·Copilot·본인의 로컬 LLM 등에 대응되면 좋겠다"). ADR-2026-09-17-A(BYOAI 커넥터, Proposed)의 미결 항목을 이 ADR이 구체화·확정한다. 범위는 MVP-2(ADR-2026-11-20-A) — 내장 엔진 없이 개시하므로 초기 전략 공급의 핵심 경로다.

## Context

이미 있는 것(코드 확인 2026-11-20): MCP `submit_proposal`(`src/api/mcp/tools_propose.py`, AI-16), `ExternalAgentProvider`(외부 에이전트가 쓴 제안을 그대로 받음), 에이전트별 토큰(`/ai/tokens`), 제안이 사람이 쓴 전략과 같은 검증 경로(컴파일·미래 참조 금지·백테스트·컴플라이언스)를 타는 구조.

문제: 지금은 MCP 클라이언트만 가정한다. 서비스마다 연결 방식이 다르다(조사 2026-11, 출처는 아래 표). **"MCP 하나"로는 못 붙는 에이전트가 있다.**

### 호환성 조사 (외부 사실 — 플랜·UI는 자주 바뀌므로 출시 전 재확인 필수)

| 에이전트 | 연결 방식 | 확인한 사실 / 불확실한 점 |
|---|---|---|
| Claude | 원격 MCP 커넥터(Claude Code·Desktop·웹) | 이 저장소의 기존 경로. 최신 UI 경로는 출시 전 확인 |
| ChatGPT | 개발자 모드의 사용자 지정 MCP 커넥터(원격, HTTPS) | 개발자 모드에서 MCP 전체(읽기·쓰기) 지원, 원격 서버는 커스텀 커넥터로 추가, SSE·스트리밍 HTTP, OAuth 또는 인증 없음. **플랜별 쓰기 가능 범위는 출처마다 다름**(Business/Enterprise/Edu만 쓰기라는 글과 Pro 포함이라는 글) — 공식 플랜표 미확인. 관리자가 개발자 모드를 켜야 하는 조직 플랜이 있음 |
| Gemini | Gemini CLI(stdio·SSE·Streamable HTTP, OAuth 2.0), Gemini API는 함수 호출 별도 경로 | API의 원격 MCP 직접 연결(Streamable HTTP만)은 제3자 글만 확인, 공식 미확인. Gemini CLI 개인 계정 제공 종료(2026-06-18) 주장도 단일 제3자 출처 |
| Copilot (GitHub) | VS Code·JetBrains·Eclipse·Xcode·Copilot CLI에서 원격 MCP(OAuth 또는 PAT) | VS Code는 GA, 나머지는 미리보기였던 문서도 있음. **Copilot 클라우드 에이전트는 OAuth 원격 MCP 미지원**이라는 기재 |
| Copilot (Microsoft) | Copilot Studio에서 MCP 서버 URL+인증 입력, 도구 목록 자동 조회 | 생성형 오케스트레이션이 켜져 있어야 함. Microsoft 365 Copilot 자체의 MCP 연결은 미확인 |
| 로컬 LLM | Ollama는 MCP 클라이언트가 아니다(툴 호출 API만 제공) → 별도 클라이언트(mcp-client-for-ollama, Goose, Cline, Continue 등) 필요. LM Studio는 2026 초 MCP 지원 주장(공식 미확인), llama.cpp는 웹 UI 쪽 | 모델마다 툴 호출 신뢰도가 다르고 양자화에 따라 다르다. 낮은 온도(0.1 이하) 권고. 정확한 모델·태그로 직접 테스트해야 함 |

또 하나: MCP 프로토콜은 2026-07에 무상태(stateless) 방향 개정이 공개됐다(GitHub MCP 서버 변경 공지). 스펙 버전 추적이 필요하다.

출처: [ChatGPT 개발자 모드 MCP](https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt) · [OpenAI 개발자 문서](https://developers.openai.com/api/docs/guides/developer-mode.md) · [Gemini CLI MCP](https://geminicli.com/docs/tools/mcp-server/) · [GitHub Copilot MCP 개요](https://docs.github.com/en/copilot/concepts/context/mcp) · [Copilot Studio MCP](https://learn.microsoft.com/en-us/microsoft-copilot-studio/agent-extend-action-mcp) · [mcp-client-for-ollama](https://pypi.org/project/mcp-client-for-ollama/0.24.1/) · [GitHub MCP 차기 스펙 공지](https://github.blog/changelog/2026-07-23-github-mcp-server-supports-the-next-mcp-specification/)

## Decision

### D1. 방향을 구분한다: 인바운드만 MVP-2

- **인바운드(에이전트가 AIOS에 접속)**: 사용자의 에이전트가 자기 쪽에서 돌며 AIOS의 도구를 호출한다. AIOS는 **사용자의 LLM API 키를 저장하지 않고** LLM 비용도 내지 않는다. 이것이 MVP-2다.
- **아웃바운드(AIOS가 사용자의 키로 LLM을 호출, BYOK)**: 키 보관·예산·철회 문제가 크다(ADR-2026-09-17-A가 Proposed로 남긴 이유). **MVP-3(내장 엔진과 함께)**으로 미룬다.

### D2. 채널 4단: 공통분모부터 쌓는다

| 단 | 채널 | 대상 |
|---|---|---|
| T1 | 원격 MCP(Streamable HTTP) + 토큰 인증 | Claude, ChatGPT 개발자 모드, Gemini CLI, Copilot(VS Code·CLI·Studio), MCP 클라이언트를 붙인 로컬 LLM |
| T2 | REST + OpenAPI 3.1(Bearer 토큰) | MCP를 못 쓰고 HTTP 호출은 되는 에이전트(예: GPT Actions류, 사용자 스크립트) |
| T3 | **도구 스키마 내보내기** — OpenAI 함수 호출 JSON, Anthropic tool JSON, Gemini 함수 선언 | 자체 에이전트 루프를 쓰는 개발자, Ollama·vLLM처럼 MCP가 아니라 툴 호출 API만 있는 로컬 스택 |
| T4 | **에이전트 없이도 쓰는 경로** — `StrategyProposal`/DSL을 화면에 붙여 넣기·업로드, 복사 가능한 프롬프트 팩 | 어떤 에이전트든(채팅창에서 결과를 복사해 붙여 넣기), 연결이 안 되는 환경 |

T4는 모든 에이전트에 100% 통하는 **최후 보증 경로**다. 어떤 연결이 막혀도 사용자는 전략을 AIOS에 넣을 수 있다.

### D3. 단일 원천(Single source of truth)

capability 정의(이름·인자 스키마·설명·예시·오류 코드) 하나에서 MCP 도구, OpenAPI, 함수 스키마 세 가지를 **생성**한다. 손으로 세 벌을 유지하지 않는다(드리프트 방지). 생성물이 서로 어긋나면 CI가 적색이다(계약 테스트).

### D4. 약한 모델·로컬 모델에 맞춘 도구 설계

로컬·소형 모델은 도구 호출 형식을 자주 틀린다. 그래서:
- **도구 수를 작게**(핵심 8개 이하) 유지하고 인자는 평평한 구조로 둔다. 큰 제안 본문은 하나의 JSON 필드로 받는다.
- **`validate_proposal`(드라이런) 도구**: 제출 전에 형식·데이터 범위·위반을 기계가 읽는 수정 힌트와 함께 돌려준다. 약한 모델은 이 피드백으로 고쳐 다시 시도한다.
- 설명에 **실제 예시**를 넣고, 오류 메시지는 "무엇이 틀렸고 어떻게 고치는지"를 준다.
- 멱등 키, 크기 상한, 호출 횟수 상한을 서버가 강제한다(모델이 재시도를 반복해도 안전).
- "라이트 프로필": 모델 등급에 따라 도구 설명·예시를 짧게 줄인 버전을 제공한다.

### D5. 인증

- **MVP-2 1차: 에이전트별 토큰(Bearer)**. 발급 시 한 번만 표시, 범위(scope) 지정, 폐기 가능, 만료.
- **MVP-2 2차: OAuth 2.1(PKCE) + 동의 화면**. ChatGPT·Copilot 일부 경로가 OAuth를 요구하거나 선호한다. 범위 동의 화면에 "실거래·출금·위임장 변경은 불가"를 명시한다.
- 토큰과 OAuth 모두 **같은 capability 범위**를 쓴다.

### D6. 보안 (ADR-2026-09-17-A D1 유지 + 보강)

- 허용 범위는 `read`·`research`·`propose`·`paper`뿐이다. `LIVE`·출금·위임장 변경·킬스위치 해제는 어떤 채널로도 불가.
- 도구 결과(시세·뉴스·공시 본문)는 **신뢰할 수 없는 데이터**로 표시하고 지시문으로 해석되지 않게 한다(프롬프트 주입이 실행 권한으로 번지지 않게). 모의 실행은 사용자 승인 화면과 일회용 확인 절차를 거친다.
- 에이전트 신원(어느 토큰·어느 채널)을 감사 로그에 남기고, 에이전트별 속도·예산 상한을 둔다. 이상 징후는 외부 보안 플러그인(SECURITY_PROGRAM.md)이 거부 신호를 보낼 수 있다.
- 로컬 LLM: 데이터가 사용자 본인 기기에서 처리됨을 안내하고, 클라우드 에이전트에는 전략·시세가 해당 업체로 전송된다는 고지를 화면에 둔다.

### D7. 지원 등급과 검증

| 등급 | 뜻 |
|---|---|
| Verified | 실제 클라이언트에서 우리가 연결·제안 제출까지 확인했다(검증일·버전 기록) |
| Compatible | 프로토콜 표준에 맞지만 실제 클라이언트로는 미검증 |
| Manual | T4(붙여 넣기)로만 보장 |

- **적합성 시험(conformance suite)**: 채널(T1~T3)마다 스크립트 클라이언트로 접속→검증→제출→오류 복구를 자동 시험한다(CI).
- **실제 서비스 확인은 수동**(출시 전·분기마다): 벤더 UI·플랜이 바뀌므로 자동화가 어렵고, 결과를 호환성 표에 날짜와 함께 기록한다.
- 에이전트별 **연결 가이드**(스크린샷 없이 단계 문장 + 복사용 설정)와 **프롬프트 팩**을 문서로 제공한다.

### D9. 호환성 유지 자동화 (사용자 질문 2026-11-20 — "플랜·UI가 자주 바뀐다면 자동화하자")

원칙: **자동화는 등급을 내리기만 하고, 올리는 것은 실제 클라이언트 확인 증거가 있을 때 사람이 한다.** 그래서 표가 낡은 채 Verified로 남지 않는다.

자동화하는 것:
1. **등록부**(`docs/ops/byoai_compat.json`): 에이전트별 등급·확인일·근거·출처·의존 사항. 표(`docs/ops/BYOAI_COMPAT.md`)는 `scripts/byoai_compat.py --render`로 생성하고 CI가 최신인지 검사한다. 근거(날짜·evidence) 없는 Verified는 형식 오류다.
2. **기한 강등**: 확인 후 90일(항목별 조정 가능)이 지나면 "재확인 필요"로 표시하고 소견을 올린다(`--overdue`).
3. **벤더 문서 변경 감시**(구현 예정): 등록부 `sources`의 공식 문서 페이지를 주기적으로(주 1회 이하) 가져와 내용 해시가 바뀌면 해당 에이전트를 재확인 대상으로 올린다. 공개 문서만, robots·이용약관을 지킨다.
4. **프로토콜 적합성 시험**(구현 예정): 공식 MCP 클라이언트 라이브러리로 우리 서버를 CI에서 매일 시험한다(스펙·라이브러리 버전 변화 감지).
5. **로컬 모델 시험**(구현 예정): 우리가 보유한 로컬 모델로 `validate_proposal`·`submit_proposal` 도구 호출 성공률을 정기 측정해 기록한다.
6. **CLI형 클라이언트**(구현 예정): 헤드리스로 돌릴 수 있는 클라이언트(Claude Code 등)는 전용 시험 계정으로 분기마다 자동 실행한다. 계정·키는 사용자가 따로 제공한다.

자동화하지 않는 것: ChatGPT 웹 화면, 조직 플랜 설정, Copilot Studio 같은 **소비자 UI**는 계정·로그인·봇 방지가 걸려 있어 자동화하지 않는다. 대신 사람이 한 줄 명령으로 돌리는 점검 스크립트와 체크리스트로 노력을 줄이고, 결과는 증거와 함께 등록부에 기록한다.

### D10. 연동 장애 진단·보고·유지보수 인계 (사용자 요구 2026-11-20 — "외부 에이전트가 바뀌어 연동이 안 되면 진단하고 알려야, 나중에 플러그인으로 붙은 DevEngine이 자동 유지보수한다")

외부 에이전트의 업그레이드·정책 변경은 우리 통제 밖이다. "깨졌다는 사실"과 "왜 깨졌는지"를 **기계가 읽는 형태**로 남겨야 사람도, 함대도, 나중에 DevEngine 플러그인도 같은 입력으로 고칠 수 있다.

**1) 관측**
- 모든 연결에서 클라이언트 신원(MCP `initialize`의 `clientInfo`·`protocolVersion`, User-Agent, 채널 T1~T4)과 결과를 에이전트별로 집계한다: 연결 성공률, 인증 실패, 도구 호출 형식 오류(스키마 불일치), 시간 초과, 제안 제출 성공률. 토큰·전략 본문은 기록하지 않는다(마스킹, 보존 기간 제한).
- **카나리(합성 시험)**: D9의 적합성 시험·로컬 모델 시험·CLI 시험이 같은 신호를 만든다. 실사용 트래픽이 적어도 변화를 잡는다.

**2) 진단 분류기** — 증상에서 원인 후보를 신뢰도와 함께 낸다.

| 원인 범주 | 단서 |
|---|---|
| 스펙·프로토콜 변경 | `protocolVersion` 협상 실패, 새 필수 헤더·메서드 |
| 인증 변경 | OAuth 흐름·PKCE·토큰 형식 오류가 한 클라이언트 버전부터 급증 |
| 도구 스키마 비호환 | 클라이언트가 우리 스키마 키워드를 거부, 인자 형식 오류 급증 |
| 전송 방식 변경 | SSE ↔ Streamable HTTP, 연결 단계 실패 |
| 벤더 장애 | 벤더 상태 페이지·여러 사용자 동시 실패 |
| 우리 회귀 | 실패 시작 시점이 우리 배포와 일치 |
| 플랜·정책 제한 | 특정 플랜·조직에서만 실패 |

판정 근거로 **시간대별 상관**(우리 배포, 벤더 문서 변경 감시 D9-3, 클라이언트 버전 분포)을 붙인다. 확신이 낮으면 "원인 미상 + 증거"로 보고하고 추측으로 단정하지 않는다.

**3) 보고·알림**
- 운영자(마스터·운영자 페르소나) 화면 `연결 상태`: 에이전트별 상태(ok / degraded / down / unknown), 최근 정상 시각, 진행 중인 사건. 기존 알림 채널로 degraded·down 전환을 알린다.
- 사용자에게는 영향 범위만 알린다("ChatGPT 연결에 일시 문제가 있습니다. 붙여 넣기로는 계속 사용할 수 있습니다") — T4 대체 경로를 항상 안내한다.
- 호환성 등록부(D9)에 **`health`(운영 관측)**를 둔다. 등급(`status`, 사람이 인증)과 분리한다: `health`는 관측이라 자동으로 오르내리고, `status`는 여전히 사람만 올린다.

**4) 사건 기록(incident record) — 유지보수 인계 형식**

버전을 붙인 JSON 하나가 사건의 단일 입력이다(`schema_version`). 사람의 티켓, 함대의 리프, DevEngine이 모두 같은 것을 읽는다.

```json
{
  "schema_version": 1,
  "incident_id": "byoai-2026-12-03-chatgpt-01",
  "agent_id": "chatgpt",
  "channel": "T1",
  "detected_at": "...",
  "symptom": {"metric": "connect_success_rate", "before": 0.98, "after": 0.12, "client_versions": ["..."]},
  "hypotheses": [{"category": "도구 스키마 비호환", "confidence": 0.7, "evidence": ["..."]}],
  "correlated_events": [{"kind": "vendor_doc_changed", "ref": "..."}],
  "reproduce": {"conformance_case": "tests/.../test_t1_chatgpt.py::test_tools_list"},
  "suggested_fix_class": "adapter_schema_generation",
  "blast_radius": {"affected_agents": ["chatgpt"], "fallback": "T4"},
  "acceptance": {"conformance_pass": true, "canary_pass": true}
}
```

**5) 자동 유지보수의 경계** — DevEngine 플러그인(MVP-3)이 이 입력으로 수정을 제안한다. 단, 다음을 지킨다.
- 수정 범위는 **연동 어댑터 계층**(도구 스키마 생성, 프로토콜 어댑터, 호환성 등록부, 가이드 문서)으로 제한한다. 주문·자금·인증 핵심(S-tier)과 보안 통제는 건드리지 못한다.
- 수정은 같은 CI·머지 보호를 거친다. 인수 조건은 사건 기록의 `reproduce` 시험과 카나리 통과다.
- 초기에는 **사람 승인 뒤 반영**한다. 승인 없이 자동 반영하는 범위는 실적(성공·되돌림 비율)을 보고 별도로 넓힌다. 항상 되돌릴 수 있어야 한다.
- 플러그인이 없는 MVP-2에서도 같은 사건 기록이 **우리 함대의 소견(`byoai_connector_degraded`)**으로 들어가 리프로 처리된다. 즉 DevEngine 플러그인은 이 흐름의 자동화 단계일 뿐 새 흐름이 아니다.

### D8. MVP-2 순서

1. T1(토큰) + `validate_proposal` + T4(붙여 넣기)
2. 연결 가이드 5종(Claude, ChatGPT, Gemini CLI, Copilot VS Code·Studio, 로컬 Ollama+MCP 클라이언트)과 실제 확인
3. OAuth 2.1
4. T2(OpenAPI), T3(스키마 내보내기)
5. 적합성 시험 CI와 호환성 유지 자동화(D9)
6. 연결 관측·카나리·진단 분류기·사건 기록·연결 상태 화면(D10). DevEngine 인계 인터페이스는 이때 스키마만 확정하고 플러그인 자체는 MVP-3

## Consequences

- 사용자가 어떤 에이전트를 쓰든 최소 T4로는 시작할 수 있고, 대부분의 에이전트는 T1로 붙는다.
- 외부 사실(플랜, UI, 스펙)이 자주 바뀌므로 호환성 표는 날짜가 붙은 살아 있는 문서이며, 출시 직전 재확인한다.
- 위험: (1) MCP 스펙 개정(무상태화)에 맞춰 서버를 갱신해야 한다. (2) 로컬 소형 모델은 제안 품질이 낮을 수 있다 — `validate_proposal`과 서버 쪽 검증이 품질 바닥을 지킨다. (3) 클라우드 에이전트로 전략 데이터가 나가는 프라이버시 고지가 필요하다.

## Open items
- ChatGPT 플랜별 쓰기 가능 범위, Gemini API 원격 MCP 공식 지원 여부, Microsoft 365 Copilot의 MCP 연결 — 공식 문서로 재확인(구현 착수 전).
- OAuth 인가 서버를 자체 구현할지 외부 IdP를 쓸지(보안 프로그램 S0 위협 모델과 함께 결정).

## Rollback
이 ADR을 Superseded로 두면 ADR-2026-09-17-A(Proposed)의 상태로 돌아간다. 구현은 채널 단위로 끌 수 있다.
