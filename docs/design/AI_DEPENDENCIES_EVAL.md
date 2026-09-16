# AI_DEPENDENCIES_EVAL — AI 파이프라인 라이선스·의존성 확인 (AI-3)

- 리프: AI-3 (`docs/specs/L4_ai_research_strategy_factory_v1.0.md` §9, §10)
- 근거: ADR-2026-09-05-A(모델 공급자 추상화·ML 신호 모델), spec §10 "공급자 SDK·ML 라이브러리 라이선스는
  AI-3에서 원문 확인(LightGBM MIT·PyTorch BSD·Ollama MIT로 알려져 있으나 확인 전 반입 금지)"
- 형식: CH-0(`CHART_ENGINE_FORK_EVAL.md`, b0826da) / IND-9(`INDICATOR_OSS_EVAL.md`) 원문-확인 형식 재사용.
  단 CH-0·IND-9는 **경쟁 후보 중 택1**을 채점했지만, 이 리프의 5개 패키지는 spec §2.1/§2.2/§2.5가 이미
  지목한 확정 의존성이라 경쟁 채점표는 없다 — 각 패키지를 개별로 "반입 가/조건부/불가"만 판정한다.
- 범위: **문서만.** `pyproject.toml` 의존성 추가, `src/foundation/ai/**` 코드 작성은 전부 후속 리프
  (AI-6·AI-7·AI-15·AI-20) 몫이며 이 리프에서 하지 않았다.
- 확인 대상: MCP SDK(`mcp`, AI-1/AI-15), Anthropic SDK(`anthropic`, AI-6), LightGBM(`lightgbm`, AI-19/20),
  PyTorch(`torch`, AI-19/20), Ollama 클라이언트(`ollama`, AI-7/AI-20 로컬 LLM 경로).
- 확인일: 2026-09-17. 모든 라이선스 원문은 각 패키지의 `pyproject.toml`/PyPI `project_urls.Repository`가
  가리키는 GitHub 저장소에서 `raw.githubusercontent.com`으로 직접 읽었다(요약 아님, 원문 인용). 보조 사실
  (버전·SPDX 분류자·wheel 크기·저장소 활성도)은 PyPI JSON API(`pypi.org/pypi/<name>/json`)와 GitHub REST
  API(`api.github.com/repos/<owner>/<repo>`)로 실측했다. 확인하지 못한 항목은 §5에 "미확인"으로 남기고
  추정하지 않았다.

## 0. 채점 원칙 — spec §10의 사전 경고가 게이트

> "LightGBM MIT·PyTorch BSD·Ollama MIT로 **알려져 있으나 확인 전 반입 금지**"

spec 저자 자신이 "알려진 통념"과 "원문 확인"을 구분하라고 명시했다. 이 문서의 유일한 산출물은 그 구분을
메우는 것이다: 각 패키지에 대해 (a) PyPI가 배포하는 wheel/sdist의 선언 라이선스, (b) 그 라이선스가
가리키는 저장소의 `LICENSE` 원문, (c) 원문과 통념이 다른 지점(있다면)을 기록한다.

라이선스 자유도가 최상위 게이트라는 점은 CH-0/IND-9와 동일하다: **AIOS는 public 모노레포이지만 루트에
`LICENSE` 파일이 없다(OSS 라이선스 미선언 = 저작권 유보, IND-9 §0 판단 재확인). 제품은 테넌트 인증 뒤의
유료 SaaS다.** 따라서 카피레프트 전파 의무(GPL/LGPL류)가 있는 패키지는 이 리프에서 즉시 탈락 대상이나,
아래 5개는 전부 permissive 계열로 사전 필터링된 상태다(spec 저자가 이미 MIT/BSD로 알려진 것만 선정) —
그럼에도 원문 확인 결과는 5개 전부 실제로 permissive임을 재확인했고, PyTorch는 통념("BSD")이 가리키지
않는 부수 의무(하단 §2.4)를 실제로 갖고 있었다.

## 1. 라이선스 원문 확인 (조항 원문 인용)

### 1.1 MCP SDK (`mcp`, PyPI 2.2.0, `github.com/modelcontextprotocol/python-sdk`)
- PyPI 선언: `License: MIT`, classifier `License :: OSI Approved :: MIT License`.
- 저장소 `LICENSE` 원문(전문, 요약 아님):
  > "MIT License / Copyright (c) 2024 Anthropic, PBC / Permission is hereby granted, free of charge, to
  > any person obtaining a copy of this software and associated documentation files (the "Software"), to
  > deal in the Software without restriction, including without limitation the rights to use, copy,
  > modify, merge, publish, distribute, sublicense, and/or sell copies of the Software... The above
  > copyright notice and this permission notice shall be included in all copies or substantial portions
  > of the Software."
- GitHub `licensee`: `MIT`. 저장소에 별도 `NOTICE` 파일 없음 → 전파 의무 없음.
- 결론: **원문·통념 일치.** 재배포·수정·상용 사용 전부 허용, 부담은 저작권고지 보존뿐.

### 1.2 Anthropic SDK (`anthropic`, PyPI 1.6.0, `github.com/anthropics/anthropic-sdk-python`)
- PyPI 선언: `License: MIT`, classifier `License :: OSI Approved :: MIT License`.
- 저장소 `LICENSE` 원문(전문): "Copyright 2023 Anthropic, PBC." + MIT 표준 본문(1.1과 동일 조항). `NOTICE`
  파일 없음.
- 결론: **원문·통념 일치.** 의무 최소.

### 1.3 LightGBM (`lightgbm`, PyPI 4.7.0, `github.com/lightgbm-org/LightGBM` — 舊 `microsoft/LightGBM`,
  2026년 중 `lightgbm-org` 조직으로 저장소 이전. `github.com/microsoft/LightGBM`는 301로 리다이렉트되며
  코드 자체가 사라진 것은 아니다.)
- PyPI 메타데이터에 `license`/`license_expression`/classifier가 전부 비어 있다(신형 PyPI 메타데이터 필드
  미기재 — pandas-ta류 provenance 결함과 달리 배포 자체가 문제는 아니고 `pyproject.toml` 선언 누락).
  wheel/sdist에 `license_files` 필드도 없어 **원문 확인은 저장소 `LICENSE`로만 가능**.
- 저장소 `LICENSE` 원문(전문): "The MIT License (MIT) / Copyright (c) Microsoft Corporation / Copyright
  (c) The LightGBM developers / Permission is hereby granted, free of charge..." — 1.1과 동일 MIT 표준
  본문. GitHub `licensee`: `MIT`.
- LightGBM Python 패키지는 TA-Lib과 유사하게 **C++ 코어를 컴파일해 바이너리로 번들**한다(win_amd64 wheel
  1.4MB, `lib_lightgbm.dll` 정적 포함). C++ 코어와 Python 바인딩이 **같은 저장소, 같은 LICENSE 파일** 아래
  있음을 저장소 구조로 확인했다 — TA-Lib처럼 래퍼/코어 라이선스가 분리돼 있지 않다.
- 결론: **원문 확인 완료, 통념(MIT)과 일치.** PyPI 메타데이터 공백은 반입 차단 사유가 아니다(원문이
  저장소에 명확히 존재).

### 1.4 PyTorch (`torch`, PyPI 2.14.0, `github.com/pytorch/pytorch`)
- PyPI `license_expression`(SPDX): `Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND
  BSD-3-Clause AND BSL-1.0 AND MIT` — **단일 라이선스가 아니라 복합 표현식**이다. `license_files` 필드에
  루트 `LICENSE` 외에 `third_party/**` 하위 **90개** 개별 `LICENSE` 파일이 나열된다(FP16, XNNPACK,
  cutlass, flash-attention, protobuf, onnx, pybind11, sleef 등).
- 루트 저장소 `LICENSE` 원문(전문, `curl raw.githubusercontent.com/pytorch/pytorch/main/LICENSE`로 확인):
  > "From PyTorch: / Copyright (c) 2016- Facebook, Inc (Adam Paszke) ... [다수 기여자 귀속] ... From
  > Caffe2: / Copyright (c) 2016-present, Facebook Inc. All rights reserved. ... Redistribution and use in
  > source and binary forms, with or without modification, are permitted provided that the following
  > conditions are met: 1. Redistributions of source code must retain the above copyright notice... 2.
  > Redistributions in binary form must reproduce the above copyright notice... 3. Neither the names of
  > Facebook, Deepmind Technologies, NYU, NEC Laboratories America and IDIAP Research Institute nor the
  > names of its contributors may be used to endorse or promote products derived from this software
  > without specific prior written permission. THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND
  > CONTRIBUTORS "AS IS"..."
  이는 **BSD 3-Clause**다(3항 "명칭 사용 금지" 조항 포함 — spec §10의 "PyTorch BSD" 통념과 일치).
- **통념과 다른 지점(원문 확인으로 새로 드러남):** GitHub `licensee`가 이 저장소를 `NOASSERTION`(자동 분류
  불가)으로 판정한다 — 루트 `LICENSE`가 PyTorch(BSD-3 계열)와 Caffe2(별도 귀속 모델, "각 기여자가 자신의
  기여분에 대한 저작권 보유") 두 프로젝트 합병 이력을 한 파일에 담고 있어 단일 SPDX 식별자로 환원되지
  않는다. 게다가 wheel은 third_party 하위 90개 컴포넌트(Apache-2.0·BSL-1.0·MIT 등 혼재)를 **정적으로
  컴파일해 바이너리에 포함**한다(win_amd64 wheel 124.1MB — MCP SDK 대비 340배, LightGBM 대비 89배).
- 결론: **원문 확인 완료, 핵심 라이선스는 BSD-3(통념과 일치)이나 의무 무게는 LightGBM/MCP/Anthropic보다
  훨씬 무겁다.** 재배포·수정 자체는 permissive이지만, AIOS가 torch를 포함해 재배포(예: 온프레미스 배포판)
  할 경우 루트 BSD-3 고지 + third_party 90개 컴포넌트 고지 전부를 동봉해야 조건을 충족한다. AI-19/20이
  torch를 실제로 반입하는 시점에 `docs/design/THIRD_PARTY_NOTICES.md`(또는 동등 파일)에 이 목록을 옮겨
  적어야 한다(IND-10이 TA-Lib에 대해 요구한 것과 동일 패턴).

### 1.5 Ollama 클라이언트 (`ollama`, PyPI 0.6.2, `github.com/ollama/ollama-python`)
- PyPI `license_expression`: `MIT`.
- 저장소 `LICENSE` 원문(전문): "MIT License / Copyright (c) Ollama" + MIT 표준 본문(1.1과 동일 조항).
  GitHub `licensee`: `MIT`. `NOTICE` 파일 없음.
- 주의: spec §2.2는 로컬 LLM을 **OpenAI 호환 HTTP 엔드포인트**(`adapters/openai_compatible_provider.py`)
  로 통일해 붙이라고 명시한다 — 이 `ollama` PyPI 패키지(공식 Python 클라이언트, Ollama 자체 REST API용)는
  AI-7의 기본 경로가 아니다. 이 패키지는 AI-3 task 제목이 명시적으로 지정했으므로 원문 확인 범위에는
  포함했으나, **채택 여부는 AI-7 구현 시점의 설계 결정**(OpenAI 호환 경로로 충분하면 이 패키지 자체를
  의존성에 추가할 필요가 없을 수 있다)이며 이 문서가 그 결정을 대신하지 않는다.
- 결론: **원문 확인 완료, 통념(MIT)과 일치.** 의무 최소.

## 2. 저장소 활성도 (참고 지표, 게이트 축은 아님)

GitHub REST API로 확인(2026-09-17 기준):

| 패키지 | 저장소 | ★ | 최근 push | archived |
|---|---|---|---|---|
| mcp | modelcontextprotocol/python-sdk | 24,314 | 2026-09-16 | false |
| anthropic | anthropics/anthropic-sdk-python | 3,904 | 2026-09-15 | false |
| lightgbm | lightgbm-org/LightGBM | 18,770 | 2026-09-13 | false |
| torch | pytorch/pytorch | 103,059 | 2026-09-16 | false |
| ollama | ollama/ollama-python | 10,534 | 2026-09-16 | false |

5개 전부 활발(30일 이내 push, archived=false) — 유지보수 축에서 탈락 사유 없음.

## 3. 런타임 호환 (실측: PyPI JSON `requires_python` + wheel 목록)

| 패키지 | `requires_python` | 저장소 `>=3.10` 요구(repo `pyproject.toml`)와 호환 | win_amd64 wheel 존재 |
|---|---|---|---|
| mcp | `>=3.10` | 가 | 365.7 KB (pure-Python, `py3-none-any`) |
| anthropic | `>=3.10` | 가 | 1,249.1 KB (pure-Python, `py3-none-any`) |
| lightgbm | `>=3.10` | 가 | 1.4 MB (cp310 전용 바이너리) |
| torch | `>=3.10` | 가 | 124.1 MB (cp310 전용 바이너리, CUDA 미포함 CPU 빌드로 추정 — 별도 `nvidia-*` 패키지 의존 없음을 wheel 메타데이터로 확인) |
| ollama | `>=3.8` | 가(하한이 저장소보다 낮아 문제 없음) | 15.1 KB (pure-Python, `py3-none-any`) |

pandas-ta(IND-9에서 Python ≥3.12 요구로 반입 자체가 거부됐던 사례)와 달리, 5개 전부 현재 venv
(Python 3.10.11)와 호환된다. torch의 124.1MB는 AI-19/20 착수 시 CI 캐시·아티팩트 크기 예산에 반영해야
할 실측치로 남긴다(이 문서 범위 밖 — CI 배선은 후속 리프).

## 4. 결론 — 리프별 반입 판정

| 패키지 | 판정 | 근거 | 착수 조건(해당 리프에서) |
|---|---|---|---|
| **mcp** | 반입 가 | MIT, NOTICE 없음, 활발, 3.10 호환 | 없음 |
| **anthropic** | 반입 가 | MIT, NOTICE 없음, 활발, 3.10 호환 | 없음 |
| **lightgbm** | 반입 가 | 원문 MIT(저장소 확인, PyPI 메타데이터 공백은 무해), 활발, 3.10 호환 | 없음 |
| **torch** | 조건부 가 | 핵심 BSD-3는 permissive이나 third_party 90개 컴포넌트 고지 의무가 있다 | AI-19/20 반입 시 `THIRD_PARTY_NOTICES.md`(또는 동등 파일)에 루트 BSD-3 + third_party 목록 동봉 |
| **ollama** | 반입 가(선택적) | MIT, NOTICE 없음. 단 AI-7 기본 경로는 OpenAI 호환 HTTP이므로 실제 의존성 추가 여부는 AI-7 설계 결정 | AI-7 구현 시점에 실제로 필요한지 재확인 |

카피레프트(GPL/LGPL) 탈락 대상은 이 5개 중 없다 — spec 저자의 사전 통념(MIT/BSD)이 원문 확인으로
전부 재확인됐고, 유일한 신규 발견은 torch의 라이선스가 "BSD" 한 줄로 요약할 수 없는 복합 표현식이라는
점(§1.4)이다.

## 5. 미확인 항목 (추정하지 않음)

- LightGBM `microsoft/LightGBM` → `lightgbm-org/LightGBM` 조직 이전의 정확한 시점·사유(현재 접근 가능한
  GitHub API 응답으로는 이전 완료 사실과 301 리다이렉트만 확인 가능, 이전 공지문 원문은 미확인).
- torch `NOASSERTION` 판정이 향후 GitHub `licensee` 버전 업그레이드로 바뀔지 여부(현재 스냅샷 기준
  기록).
- torch win_amd64 wheel이 실제로 CUDA 코드를 전혀 포함하지 않는지(바이너리 내부 실행 검증은 하지 않았고,
  wheel이 별도 `nvidia-*` 의존성을 선언하지 않는다는 메타데이터 사실만 확인했다).
- ollama-python 패키지가 AI-7에서 실제로 쓰일지 여부(§1.5 — 설계 결정 미확정, 이 문서 범위 밖).

## 6. 테스트 가능성에 대한 메모 (D2 체크리스트 미충족 사유)

이 리프는 CH-0·IND-9와 동일하게 **문서 산출물뿐**이며 코드 변경이 0이다(`git diff --stat` 대상은
`docs/design/AI_DEPENDENCIES_EVAL.md` 1개 파일뿐). ADR-2026-09-09-C의 D2 하한(negative test ≥3, 실패
주입 1, 성능 단언 1, 게이트 적색 재현 1)은 **N/A(테스트 가능한 코드가 없음)** — CH-0(task-1133)이
`DEPTH_CH.md` 감사에서 "문서+측정스크립트 리프라 테스트 인프라 자체가 없음"으로 D0 판정을 받은 것과
동일한 구조적 사유다. 이 문서 자체에는 벤치 스크립트도 없다(CH-0과 달리 AI-3 DoD는 "CH-0 형식"만
요구하고 별도 실측 스크립트를 요구하지 않는다 — spec §9 AI-3 행 참고). depth = D0(N/A, 문서 전용 리프).

## 7. 게이트

이 문서가 AI-3의 전체 산출물이다. **AI-19/20(ML 신호 모델의 LightGBM/torch 실제 반입)과 AI-7(로컬 LLM
경로에서 ollama 클라이언트 채택 여부)은 이 문서의 §4 판정을 전제로 진행하며, torch를 실제로
`pyproject.toml`에 추가하는 시점에 §1.4의 third_party 고지 의무를 이행해야 한다.**
