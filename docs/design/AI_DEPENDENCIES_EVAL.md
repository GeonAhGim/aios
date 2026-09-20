# AI_DEPENDENCIES_EVAL — AI 파이프라인 라이선스·의존성 평가 (AI-3)

- 근거: `docs/specs/L4_ai_research_strategy_factory_v1.0.md` §2·§9·§10.
- 형식: CH-0의 원문 근거 → 평가 축 → 비교표 → 채택 조건 → 검증 순서.
- 재확인일: 2026-09-20. 아래 공식 저장소 LICENSE 원문을 웹 도구로 열어 확인했다.
- 범위: 평가 문서와 문서 회귀 테스트. 의존성 설치·교체 및 FROZEN 영역 변경 없음.
- 이전 문서의 버전·별 수·wheel 크기·Python 호환성·90개 고지 수치는 재현 자료가 없어 철회한다.
  저장소 이전, NOTICE 부재, 모든 번들 구성요소의 라이선스도 이번 확인 사실에 포함하지 않는다.

## 0. 평가 원칙

라이선스 원문 확인이 최우선이다. 확인 불가·조건 불충족이면 반입 보류한다.
다섯 패키지는 서로 대체 후보가 아니므로 총점으로 하나를 선택하지 않는다.
CH-0의 차트 fps·드로잉·TS 축은 N/A(서버 SDK·ML 의존성 평가)이며 아래 축으로 대체한다.

| 축 | 값 | 판정 방법 |
|---|---|---|
| L: 루트 라이선스 | 확인 / 미확인 | 공식 LICENSE 원문과 고지 의무를 기록 |
| A: 배포 아티팩트 | 확인 / 미확인 | 사용할 버전·해시·번들 및 전이 의존성 고지를 확인 |
| R: 실행 호환성 | 확인 / 미확인 | 대상 Python·OS에서 설치·import·최소 호출 검증 |
| 결론 | 조건부 / 보류 | L 확인이면 조건부 후보, L 미확인이면 보류. A/R 미확인은 설치 승인 아님 |

루트의 허용적 라이선스만으로 모델 가중치·데이터·API 서비스 약관까지 승인하지 않는다.
GPL/LGPL 등 별도 검토가 필요한 조건을 발견하면 제품 배포 방식과 함께 재검토하고 자동 승인하지 않는다.

## 1. 라이선스 원문과 의무

링크는 브랜치 HEAD이므로 변경될 수 있다. 아래 확인은 조회 당시 루트 원문에 한정한다.
실제 반입 시 태그/커밋 SHA와 배포 파일 SHA-256을 별도로 고정해야 한다.
짧은 인용은 전문을 대체하지 않으며 배포 시 원문 전체와 해당 고지를 보존한다.

### 1.1 MCP Python SDK (`mcp`)

- [공식 LICENSE](https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/main/LICENSE)
- MIT. 귀속: Anthropic, PBC (2024).
- 고지 조건 원문: “The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.”
- 상용 사용·수정·재배포 허용 조항을 확인했다. 저작권·허가 고지 및 면책 본문을 보존한다.
- AI-1/AI-15의 Python SDK 대상이며 TypeScript SDK 등 별도 패키지에는 이 판정을 전용하지 않는다.

### 1.2 Anthropic Python SDK (`anthropic`)

- [공식 LICENSE](https://raw.githubusercontent.com/anthropics/anthropic-sdk-python/main/LICENSE)
- MIT 본문. 귀속: Anthropic, PBC (2023).
- 고지 조건 원문: “The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.”
- SDK의 상용 사용·수정·재배포 시 저작권·허가 고지와 면책을 보존한다.
- AI-6 대상. SDK 라이선스는 유료 API 이용 조건·데이터 처리 조건을 대신하지 않는다.

### 1.3 LightGBM (`lightgbm`)

- [공식 LICENSE](https://raw.githubusercontent.com/microsoft/LightGBM/master/LICENSE)
- MIT. 귀속: Microsoft Corporation 및 The LightGBM developers.
- 고지 조건 원문: “The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.”
- 루트 원문은 상용 사용·수정·재배포를 허용한다. Python 배포물의 네이티브 라이브러리와
  전이 의존성까지 동일 조건이라고 추정하지 않고 실제 배포물별 고지를 확인한다.
- AI-19/20 대상. PyPI 메타데이터 누락 여부·wheel 내용은 이번에 미검증이다.

### 1.4 PyTorch (`torch`)

- [공식 LICENSE](https://raw.githubusercontent.com/pytorch/pytorch/main/LICENSE)
- BSD-3-Clause 계열 본문과 PyTorch/Caffe2 및 여러 기여자 귀속을 확인했다.
- §1 원문: “Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.”
- §2는 바이너리 재배포 시 문서 등 배포 자료에 저작권·조건·면책 재현을 요구한다.
  §3은 사전 서면 허가 없이 명칭을 제품 보증·홍보에 사용하는 것을 금지한다.
- AI-19/20 대상. 루트 BSD 조건은 wheel 전체에 대한 단일 라이선스 판정이 아니다.
  third_party 및 CPU/CUDA 등 선택한 배포물의 구성요소별 LICENSE/NOTICE를 확인하여
  `THIRD_PARTY_NOTICES.md` 또는 동등한 배포 고지에 반영해야 한다. 개수는 미검증이다.

### 1.5 Ollama Python 클라이언트 (`ollama`)

- [공식 LICENSE](https://raw.githubusercontent.com/ollama/ollama-python/main/LICENSE)
- MIT. 귀속: Ollama.
- 고지 조건 원문: “The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.”
- 클라이언트의 상용 사용·수정·재배포 시 저작권·허가 고지와 면책을 보존한다.
- Ollama 서버·다운로드 모델 가중치의 라이선스는 별도 확인 대상이다.
  spec §2.2의 AI-7 기본 경로는 OpenAI 호환 HTTP이므로 이 클라이언트 설치는 필수가 아니다.

## 2. 런타임·의존성 확인 범위

로컬 `pyproject.toml`은 Python `>=3.10`을 요구하며 `lightgbm>=4.7,<5.0`을 이미 선언한다.
나머지 네 패키지는 직접 의존성 목록에 없다. 이는 설치 환경의 전체 패키지 목록을 뜻하지 않는다.
LightGBM 선언 옆의 기존 “no conditions attached” 주석은 이번 §4의 조건부 판정 근거가 아니다.
이 문서는 기존 설치를 검증한 것으로 간주하지 않으며 해당 선언은 이 작업에서 변경하지 않는다.

아래 항목은 전부 미검증으로 남긴다: 선택 버전의 Requires-Python, Windows/Linux 및 CPU/GPU
wheel 가용성, 전이 의존성 충돌, 설치 용량, import/호출 성공, 취약점 상태와 유지보수 활성도.
버전 범위가 존재한다는 사실만으로 위 조건을 통과했다고 판단하지 않는다.

## 3. CH-0 비교표

| 패키지 | 루트 라이선스 | L | A | R | 판정 |
|---|---|---|---|---|---|
| mcp | MIT | 확인 | 미확인 | 미확인 | 조건부 |
| anthropic | MIT | 확인 | 미확인 | 미확인 | 조건부 |
| lightgbm | MIT | 확인 | 미확인 | 미확인 | 조건부 |
| torch | BSD-3-Clause 계열 | 확인 | 미확인 | 미확인 | 조건부 |
| ollama | MIT | 확인 | 미확인 | 미확인 | 조건부 |

## 4. 결론 — 후속 리프 반입 조건

루트 라이선스는 다섯 패키지 모두 허용적이다. **미확인 아티팩트의 무조건 반입은 금지한다.**
AI-6·AI-15는 각각 SDK 경계에서, AI-19/20은 ML 어댑터 경계에서 다음 증거를 남긴다.

1. 사용할 버전과 저장소 태그/커밋, wheel/sdist 출처 및 SHA-256을 기록한다.
2. 해당 버전 LICENSE 전문·NOTICE·번들/전이 의존성을 조사하고 배포 고지를 보존한다.
   torch는 CPU/CUDA 배포 선택에 따라 재검토하며 모델 가중치는 따로 판정한다.
3. 대상 Python·OS에서 설치·import·최소 호출을 검증하고 충돌·실패 로그도 기록한다.
4. 확인 불가 또는 의무 미충족은 반입 보류한다. AI-7의 ollama는 HTTP 경로로 충분한지 먼저 판단한다.

## 5. 불변조건 확인

I-01~I-05·I-09: 실행·주문·장부·아티팩트 코드 변경 없음.
I-06·I-08: SDK 허용이 인간 세션 권한 상속이나 MCP의 독자 인가 로직을 허용하지 않는다.
I-07·I-10: 이 문서는 정책 엔진 또는 런타임 강제 게이트가 아니다. 아래 테스트는 문서 계약만 검사한다.
I-11: 라이선스 판정은 PAPER 실행의 1회성 확인 토큰 요건을 대체하지 않는다.

## 6. 검증 및 한계

`tests/unit/scripts/test_ai_dependencies_eval.py`는 실제 문서의 다섯 출처·평가표·반입 조건을 검사한다.
출처 삭제, 패키지 행 삭제, 미확인 상태의 무조건 승인 변조를 각각 거부하는 negative test를 포함한다.
외부 네트워크와 패키지 설치 없이 재현할 수 있으며 외부 원문의 진위·미래 변경을 보증하지 않는다.

실행: `python -m pytest -q -p no:cacheprovider tests/unit/scripts/test_ai_dependencies_eval.py`.
변경한 Python 테스트는 `python -m py_compile tests/unit/scripts/test_ai_dependencies_eval.py`로 검사한다.
D2 실패 주입은 문서 변조로 검증한다. 성능 수치·실행 게이트 적색·replay_verify는
N/A(런타임 구현·CI 게이트를 도입하지 않는 문서 평가 리프). 새 CI 게이트 도입 없음.
