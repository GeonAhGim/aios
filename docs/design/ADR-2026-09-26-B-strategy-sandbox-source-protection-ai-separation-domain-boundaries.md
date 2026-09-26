# ADR-2026-09-26-B: 전략 실행 격리·소스 보호·AI/매매 분리 명문화·도메인 경계 계약

## Status
Accepted (2026-09-26, Chief Architect). 사용자 질문 4건에 대한 CTO 진단 후 "모두 승인".

## Context (실측 2026-09-26)
- 전략은 자체 DSL(`src/core/script`)로만 실행되고 exec/eval/subprocess 0건, 컴파일 시 정적 자원 상한(DSL-6)이 있으나
  **런타임 격리(프로세스/메모리/wall-clock 상한)가 없다** — 인터프리터 결함이나 병리적 입력 하나가 실행 프로세스를 잡을 수 있다.
- 마켓플레이스 PROTECTED 이상은 접근 제어로만 소스를 숨기고 저장은 평문(`src/foundation/marketplace/domain/visibility.py`).
- AI는 주문 경로 import 0(정적 테스트)·confirm ticket·FF_U3 기본 off로 분리돼 있으나 INVARIANTS에 명문이 없고, AI factory의
  PAPER 승격 경로가 존재하며, 로컬 LLM 추론이 매매 프로세스와 같은 호스트 자원을 경합한다.
- `.aios-zone`·import_linter 단계·ADR-2026-09-10-C가 있으나 pyproject에 import-linter 계약이 명시돼 있지 않아 도메인 간 금지 의존이 코드로 읽히지 않는다.

## Decision
1. **전략 런타임 격리(SBX-1~3)**: (SBX-1) 인터프리터에 런타임 스텝/연산 예산 카운터를 두고 초과 시 `SCRIPT_RUNTIME_LIMIT`로 중단(정적 DSL-6과 별개). (SBX-2) 백테스트·전략 평가는 별도 워커 프로세스 풀에서 실행하고 wall-clock·RSS 상한을 걸어 초과 시 프로세스를 죽이고 결과를 fail-closed로 기록. (SBX-3) 실행 경로(execution_loop)에서는 전략 평가 결과만 소비하고 전략 코드를 같은 프로세스에서 실행하지 않는다.
2. **소스 보호(SRC-1~2)**: (SRC-1) 서버는 서명된 IR(`artifact`)만 보관·실행하고 원문 소스는 저자 측/암호화 열(at-rest, tenant key)로만 보관하는 `source_retention` 옵션 — PROTECTED 이상 기본 "IR-only". (SRC-2) 실행 결과 증명은 `ReproductionKey` 해시체인으로 하며, ZK 실행은 매매 경로에 채택하지 않는다(사후 감사 증명 후보로만 기록).
3. **AI/매매 분리 명문화(AIS-1~2)**: (AIS-1) INVARIANTS.md에 I-12 "AI/에이전트는 LIVE 주문 경로에 도달할 수 없고, PAPER 승격·실행은 confirm ticket 필수"를 추가하고 정적 테스트(`test_no_execution_access`)를 그 불변식의 검사로 등록. (AIS-2) 로컬 LLM 추론(local_llm_proxy/llama.cpp)과 매매 프로세스의 자원 격리 — 별도 프로세스 우선순위·코어 고정(fleet 운영 문서 + 실행 루프 지연 SLO 측정).
4. **도메인 경계 계약(DOM-1~2)**: (DOM-1) pyproject `[tool.importlinter]`에 계약 명시: foundation 도메인 간 직접 import 금지(contracts/ports 경유만), services→foundation 단방향, domain→adapters 역의존 금지, core는 foundation을 모른다. baseline 래칫으로 warn→gate. (DOM-2) 5계층이 비어 있는 도메인은 비어 있는 이유를 zone 매니페스트에 표기(SCAFFOLD)하고 새 코드는 계층 위치를 강제.

## Consequences
- PM은 SBX-1/SRC-1/AIS-1/DOM-1을 우선 발행(저장소만으로 완결 → 클라우드 적합, 단 SBX-2·SRC-1의 실행/암호 경로는 tier S Claude 전용).
- 기준선·게이트 완화 없음. 되돌림: 이 ADR Superseded + 해당 리프 종결.
