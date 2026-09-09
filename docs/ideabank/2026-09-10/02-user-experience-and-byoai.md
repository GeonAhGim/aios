Status: REVIEW → ADR CANDIDATE (task IDEA-02; concretizes ADR-2026-09-05-A agent gateway)

# 02. 사용자 경험과 BYOAI Agent Connector

Status: IDEA / REVIEW  
Date: 2026-09-10

## 사용자 관점의 AIOS

사용자가 AIOS를 사용하면서 내부의 OMS, Risk Engine, Mandate, Ledger, Event Store, Evidence, Execution Ownership을 직접 이해할 필요는 없다.

사용자 경험은 대략 다음 흐름으로 단순화되어야 한다.

```text
계좌 연결
→ 시장 탐색
→ 전략 생성/선택
→ 검증
→ Risk/Mandate 설정
→ PAPER
→ LIVE 승인
→ 자동운용
→ 모니터링/설명/복기
```

다만 여기서 중요한 수정점이 있다.

AIOS의 AI Assistant는 AIOS가 자체 LLM 토큰을 사용자에게 제공하는 형태를 기본값으로 삼지 않는다.

## BYOAI 원칙

사용자는 본인이 이미 쓰는 AI Agent 또는 AI API를 AIOS에 연결한다.

예:

- ChatGPT / Codex
- Claude / Claude Code
- Gemini
- 기타 MCP client
- 회사 내부 Agent
- Local LLM
- Ollama
- vLLM
- LM Studio
- OpenAI-compatible endpoint

AIOS의 역할은 모델을 판매하는 것이 아니라 이 AI들이 AIOS 기능을 안전하게 사용할 수 있도록 제한된 금융 capability surface를 제공하는 것이다.

개념적으로:

> AIOS-provided AI가 아니라 AIOS Agent Gateway / Connector Experience.

## 비용 구조

사용자의 AI 비용과 AIOS 비용을 분리한다.

```text
OpenAI / Anthropic / Gemini 비용
→ 사용자 자신의 계정/API에서 부담

AIOS SaaS/Marketplace 비용
→ AIOS에 별도 지급
```

AIOS가 모델 토큰을 재판매할 필요가 없다.

이 구조는 특정 LLM 회사 종속을 줄이고, 사용자가 자신의 모델 선택권과 비용통제를 유지하게 한다.

## 연결방식 A: External Agent Connector

기본이자 권장 방식.

예:

```text
Claude / Codex / Gemini Agent
          │
          │ MCP / constrained agent protocol
          ▼
      AIOS Agent Gateway
```

외부 AI는 AIOS가 발급한 scoped Agent Token으로만 접근한다.

AIOS Agent Token은 LLM 사용권이 아니다.

이 토큰은 AI가 AIOS에서 수행할 수 있는 capability를 제한한다.

예:

```text
read
research
propose
paper
```

반대로 아래 capability는 Agent Token에 존재하지 않아야 한다.

```text
LIVE trading authority
withdraw funds
modify mandate
modify risk policy
disable kill switch
issue/revoke admin credentials
access other tenants
modify AIOS code
```

## 연결방식 B: BYOK Provider Connector

사용자가 AIOS 화면 안에서 대화형 UI를 사용하고 싶을 경우 선택형으로 제공할 수 있다.

예:

```text
OpenAI API Key
Anthropic API Key
Gemini API Key
Ollama endpoint
vLLM endpoint
```

여기에서도 AIOS 회사의 토큰을 대신 사용하는 것이 아니라 사용자의 자격증명을 통해 inference 비용이 발생한다.

보안상 원칙:

- provider credential은 암호화 저장 또는 가능한 경우 AIOS가 직접 장기 보관하지 않는 방식을 우선
- tenant 분리
- rotation
- revoke
- per-provider budget
- per-agent scope
- prompt injection이 실행권한으로 연결되지 않도록 capability isolation

## 실제 사용 예

사용자가 Claude를 AIOS에 연결했다고 가정한다.

사용자는 Claude에서 다음과 같이 말한다.

> 내 AIOS 계좌와 시장 데이터를 보고 최근 3년간 외국인 순매수와 거래량 돌파를 이용한 전략을 연구해줘.

Claude는 AIOS Agent Gateway의 `read/research` capability를 사용한다.

그다음 사용자가 말한다.

> 이 조건으로 전략 만들어줘.

Claude는 AIOS에 임의 Python 코드를 배포하지 않는다.

대신 구조화된 StrategyProposal을 제출한다.

개념적 contract:

```text
script_source
hypothesis
data_scope
params
provider_ref
prompt/version metadata
```

AIOS는 이를:

```text
schema validation
→ AIOS Script compile
→ static validation
→ resource bounds
→ no-lookahead
→ backtest
→ experiment ledger
→ risk evaluation
```

으로 검증한다.

## PAPER 승인

사용자가:

> PAPER에서 돌려.

라고 해도 외부 AI가 바로 실행시키지 않는다.

AIOS가 사용자에게 별도 승인 UI를 보여준다.

```text
Claude가 Strategy X의 PAPER 실행을 요청했습니다.

최대 운용금: 10,000,000원
예상 MDD: -8.2%
Risk Gate: PASS

[승인] [거부]
```

확인용 one-time ticket / action digest를 사용하여 preview와 execution이 같은 요청임을 보장하는 방향이 적절하다.

## LIVE

외부 AI가 LIVE 권한을 직접 갖는 구조는 기본 설계에서 배제한다.

LIVE는:

```text
Agent proposal
→ deterministic validation
→ user mandate
→ user approval
→ AIOS risk/compliance
→ controlled execution
```

으로 간다.

AI는 제안자이지 Master Authority가 아니다.

## Connector UI 예시

```text
AI Connections

Claude Code        ● Connected
Scopes:
✓ Read
✓ Research
✓ Propose
✓ Paper Request
✕ Live Trading
✕ Funds
✕ Policy Modification

Codex              ● Connected
Gemini             ○ Connect
OpenAI API         ○ Add API Key
Ollama             ○ Local
Generic MCP Agent  ○ Connect
```

## 장기 제품 포지션

이 방향이 성립하면 AIOS는:

> AI가 포함된 자동매매 서비스

보다

> 어떤 AI든 안전하게 연결할 수 있는 금융 운영체제

라는 포지션이 더 정확해진다.

모델 세대가 바뀌더라도 AIOS의 core value는 유지된다.

AIOS는 모델의 intelligence를 소유하는 대신 다음을 소유한다.

- financial capability
- market data context
- strategy contracts
- backtest/experiment infrastructure
- risk authority
- execution
- ledger
- evidence
- marketplace
- social graph
- verified track record
