# BACKTEST_VECTOR_EVAL — 벡터 백테스트 OSS 라이선스·설계 참조 평가 (BT-14)

**게이트: 이 문서가 CA 승인을 받기 전에는 BT-15/BT-16/BT-17(벡터화 엔진 및 그 위 리프)을 배정하지 않는다.**

- 리프: BT-14 (`docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md` §9.9)
- 근거: ADR-2026-09-05-A D1(OSS 어댑터 층, 카피레프트·상업 사용 제한 라이선스 코드 차용 금지) + ADR-2026-09-06-G §4(라이선스 게이트 정정 — "배지만 보면 통과하는 함정", Apache-2.0 + Commons Clause를 명시적 거부 등급으로 추가)
- 형식: CH-0(`CHART_ENGINE_FORK_EVAL.md`, b0826da) 채점표 형식과 IND-9(`docs/design/INDICATOR_OSS_EVAL.md`, task-1536 da52f3c)의 재사용 선례를 따른다. BT-14는 스코프가 좁아(후보 4종, 전부 §C 게이트가 1차 결정 요인) 가중치 채점표 대신 후보별 라이선스 원문 인용 표 + 판정으로 축소한다.
- 범위: **문서만.** 코드 변경 0, `pyproject.toml` 의존성 추가 0. BT-15~17은 이 문서가 CA 확정 전에는 배정하지 않는다.
- 확인일: 2026-09-08. 모든 라이선스 원문은 각 프로젝트의 GitHub 저장소 `raw.githubusercontent.com` 원문 파일 또는 PyPI 배포 메타데이터에서 직접 읽었다(배지·README 요약 미사용). vectorbtpro는 공개 저장소가 없는 초대제 상용 제품이라 공식 사이트의 `Software License` 페이지(`vectorbt.pro/terms/software-license/`) 원문을 인용했다. 확인하지 못한 항목은 "미확인"으로 남기고 추정하지 않았다.

## 0. 전제 — 왜 4종인가

ADR-2026-09-06-G §4는 "Apache-2.0 + Commons Clause"를 배지 트랩의 대표 사례로 지목하며 vectorbt·pybroker를 명시적으로 거명했다. 명세 §9.9 BT-14 원문은 vectorbt·zipline-reloaded 두 종만 지목하지만, decision DoD(a)가 요구하는 최소 4종을 채우기 위해 **vectorbtpro**(vectorbt의 유료 후속 상용판 — 벡터화 백테스트를 다루면서 이 문서를 건너뛰면 나중에 "그건 안 봤다"는 반론이 나올 수 있는 후보)와 **pybroker**(ADR-2026-09-06-G §4가 이미 Commons Clause 사례로 지목)를 추가했다.

## 1. 라이선스 원문 확인 (SPDX 식별자 + 원문 조항 인용 + 반입 가/부)

### 1.1 vectorbt (`polakowo/vectorbt`, PyPI `vectorbt` 1.1.0)
- **SPDX 식별자**: 공식적으로는 없다. 저장소 루트 `LICENSE.md`(https://github.com/polakowo/vectorbt/blob/master/LICENSE.md, 2026-09-08 확인)의 첫 절은 다음과 같다: `"Commons Clause" License Condition v1.0 / The Software is provided to you by the Licensor under the License, as defined below, subject to the following condition. / Without limiting other conditions in the License, the grant of rights under the License will not include, and the License does not grant to you, the right to Sell the Software.` 이어서 `Software: vectorbt / License: Apache 2.0 with Commons Clause / Licensor: Oleg Polakow` 표기 후 Apache License 2.0 전문이 이어진다. 즉 파일 하나에 "Commons Clause 조건 + Apache-2.0 본문"이 결합되어 있다.
- **"Sell" 정의 원문**: `"Sell" means practicing any or all of the rights granted to you under the License to provide to third parties, for a fee or other consideration (including without limitation fees for hosting or consulting/ support services related to the Software), a product or service whose value derives, entirely or substantially, from the functionality of the Software.`
- **배지 트랩 실측 증거**: GitHub REST API(`GET /repos/polakowo/vectorbt`, 2026-09-08)의 `license.spdx_id`는 `"NOASSERTION"`이고, PyPI `vectorbt` 1.1.0 메타데이터(`https://pypi.org/pypi/vectorbt/json`)의 `info.license`와 `classifiers`에는 라이선스 항목이 **아예 없다**. 즉 자동화 도구는 이 조합을 "Apache-2.0"으로도, 어떤 명확한 등급으로도 표시하지 못하고 침묵하거나(PyPI) NOASSERTION(GitHub)으로 넘어간다 — README 배지만 보고 "Apache-2.0이니 통과"라고 판단하면 이 침묵을 놓친다.
- **반입 가/부: 부(코드 차용 기준).** ADR-2026-09-06-G §4 금지 등급표("Apache-2.0 + Commons Clause" 등급, 사례 vectorbt)와 부합. AIOS는 테넌트 인증 뒤의 유료 SaaS이고 마켓(MP)은 "보호 소스" 판매 모델이므로, vectorbt의 벡터화 백테스트 기능을 실질적으로 흡수한 코드는 정확히 Commons Clause의 "Sell" 정의(수수료를 받고 제공하는 상품/서비스의 가치가 그 소프트웨어 기능에서 실질적으로 파생)에 해당한다.

### 1.2 vectorbtpro (`vectorbt.pro`, 초대제 상용, 공개 저장소 없음)
- **SPDX 식별자**: 없음. OSS가 아니라 독점 소스가용(source-available) 상용 라이선스다. 공식 사이트 `Software License` 페이지(https://vectorbt.pro/terms/software-license/, "Last Updated: January 5, 2025", 2026-09-08 확인) 원문:
  - §1.4 사용 범위 정의: `"Private Use" / "Non-Commercial Use" ... Organizations (including for-profit entities): Internal, in-house usage for purposes such as evaluation, data analysis, prototyping, or research and development (R&D), provided it does not result in a commercial product, service, or outcome. Any use that contributes to or is integrated into a commercial offering requires a separate commercial license.`
  - §2.1 라이선스 부여: `The Copyright Holder grants the Authorized User a limited, revocable, non-exclusive, and non-transferable license to download, install, use, copy, and modify the Software exclusively for Private Use / Non-Commercial Use`
  - §3.1 재배포 금지: `The Software must not be published, shared, uploaded to publicly accessible platforms, distributed, transmitted, or made available to any third party without prior written consent from the Copyright Holder.`
  - §3.4 리버스 엔지니어링 금지: `The Authorized User may not decompile, decrypt, reverse engineer, disassemble, or otherwise attempt to derive the source code of the Software, except where permitted by applicable law.`
- **반입 가/부: 부 (원천 거부, 논의 불필요 등급).** 애초에 "오픈소스"가 아니라 §1.4가 정의하는 비상업적 사용에만 한정된 상용 라이선스이며, AIOS는 상업적 유료 SaaS이므로 §1.4 단서("commercial offering requires a separate commercial license")에 즉시 걸린다. §3.4가 리버스 엔지니어링 자체를 금지하므로 소스에 접근할 권리도 없다.

### 1.3 zipline-reloaded (`stefan-jansen/zipline-reloaded`, PyPI `zipline-reloaded` 3.1.1)
- **SPDX 식별자**: `Apache-2.0`. 저장소 루트 `LICENSE`(https://github.com/stefan-jansen/zipline-reloaded/blob/main/LICENSE, 2026-09-08 확인) 전문이 표준 Apache License 2.0("Apache License / Version 2.0, January 2004 / http://www.apache.org/licenses/ ... TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION")이다. GitHub API(`license.spdx_id`) 실측값도 `"Apache-2.0"`으로 일치 — Commons Clause나 그 밖의 추가 조건이 **없다**.
- **의무(§4 원문)**: `You may reproduce and distribute copies of the Work or Derivative Works thereof in any medium... provided that You meet the following conditions: (a) You must give any other recipients of the Work or Derivative Works a copy of this License; ... (d) If the Work includes a "NOTICE" text file as part of its distribution, then any Derivative Works that You distribute must include a readable copy of the attribution notices contained within such NOTICE file...` 저장소에는 별도 `NOTICE` 파일이 없다(2026-09-08 확인, 404) — 즉 (d) 의무는 발생하지 않고, 원본 소스 파일 상단의 저작권 고지 유지(§4(b))와 라이선스 사본 동봉(§4(a))만 부담한다.
- **반입 가/부: 가(코드 차용 기준, 법적으로).** Apache-2.0은 상업적 이용·수정·재배포를 전부 허용한다. 단 §2 참조 — 프로젝트는 이미 ADR-2026-09-06-G §6·§7에서 zipline-reloaded의 슬리피지·수수료 모델(BT-2~6)을 "참조만 하고 직접 쓴다"(REFERENCE)로 확정했으므로, **법적으로 가능함에도 실제로는 코드를 벤더링하지 않는다**는 프로젝트 결정을 유지한다.

### 1.4 pybroker (`edtechre/pybroker`, PyPI 배포명 `lib-pybroker` 2.0.1)
- **SPDX 식별자**: 공식적으로는 없다. 저장소 루트 `LICENSE`(https://github.com/edtechre/pybroker/blob/master/LICENSE, 2026-09-08 확인) 구조는 vectorbt와 동일한 패턴이다: `"Commons Clause" License Condition v1.0 / ... the grant of rights under the License will not include, and the License does not grant to you, the right to Sell the Software.` 이어서 `Software: PyBroker / License: Apache 2.0 with Commons Clause / Licensor: Edward West` 표기 후 Apache License 2.0 전문.
- **배지 트랩 실측 증거**: PyPI `lib-pybroker` 2.0.1 메타데이터의 `info.license` 필드는 문자열 그대로 `"Apache License 2.0 with Commons Clause"`이지만, `classifiers`에는 `"License :: Free for non-commercial use"` 하나만 있고 OSI 승인 Apache-2.0 classifier는 **없다**. GitHub API `license.spdx_id`도 `"NOASSERTION"`. PyPI 페이지를 classifier 필터로만 훑으면(OSI-approved 필터 등) 이 패키지는 걸러지지 않고 새어 나올 수 있다 — "Free for non-commercial use" 문구를 읽어야만 걸린다.
- **반입 가/부: 부(코드 차용 기준).** 1.1의 vectorbt와 동일 사유. ADR-2026-09-06-G §4 금지 등급표에 pybroker가 명시적으로 나열되어 있다.

## 2. 설계 참조 vs 코드 차용 — 후보별 분리 판정

거부 라이선스라도 "공개된 API 형태를 읽고 이해해 우리 코드를 독자적으로 작성"하는 것(아이디어·인터페이스 형태 관찰)과 "소스·테스트 벡터를 그대로 복사"하는 것(표현의 복제)은 다른 문제다. 전자는 저작권이 보호하지 않는 아이디어/기능 영역이고, 후자는 라이선스 조건이 직접 규율하는 표현의 복제다.

| 후보 | 코드·테스트 벡터 차용 | 공개 API/문서 형태 설계 참조 |
|---|---|---|
| vectorbt | **금지** — Commons Clause "Sell" 조항이 유료 SaaS에 흡수된 코드를 직접 겨냥한다 | **허용** — `Portfolio.from_signals()`류 신호→포지션→체결 벡터화 파이프라인 구조, 신호 배열을 numpy 불리언/부동소수 배열로 다루는 관례는 공개 문서·README·API 레퍼런스에서 읽고 우리 코드를 독자적으로 설계하는 데 참조할 수 있다. 소스 함수 본문·유닛테스트 데이터는 옮기지 않는다 |
| vectorbtpro | **금지** — 접근권 자체가 없다(§3.1 재배포 금지, §3.4 역공학 금지로 소스 열람이 초대 회원에게만 허용) | **공개 문서 범위로 제한된 허용** — 결제 없이 공개된 마케팅 페이지·튜토리얼 목차 수준(예: "Rust 백엔드로 실행 재작성" 같은 공개 릴리스 노트)만 참조 가능. 유료 회원 전용 소스·쿡북·API 문서는 접근권이 없으므로 참조 대상에서 원천 제외 |
| zipline-reloaded | **허용(법적으로)이나 미사용** — Apache-2.0은 코드 차용을 허용하지만, ADR-2026-09-06-G §6·§7이 BT-2~6(슬리피지·수수료 모델)을 이미 "참조만 하고 직접 쓴다"로 확정했다. 이번 리프는 그 결정을 재확인만 하고 번복하지 않는다 | **허용** — 슬리피지·수수료·체결 모델의 공식·경계조건은 공개 소스에서 읽고 BT-2~6 벡터 구현(BT-15)에 우리 코드로 재작성해도 된다 |
| pybroker | **금지** — vectorbt와 동일 사유(Commons Clause) | **허용** — walk-forward/그리드 실행 오케스트레이션의 공개 인터페이스 형태(파라미터 그리드 입력→결과 원장 출력 계약)를 참조해 BT-16 설계에 반영할 수 있다. 내부 구현 코드는 옮기지 않는다 |

## 3. §C 금지 등급표와의 정합성 확인

ADR-2026-09-06-G §4(§C 금지 등급표)는 다음을 명시적 거부로 못박았다: `Apache-2.0 + Commons Clause` — 사례 `vectorbt, pybroker` — 사유 "소프트웨어 가치에 실질적으로 기대는 제품의 판매를 금지한다. AIOS가 정확히 그 경우다. 배지에는 Apache-2.0만 보인다." 본 문서 §1.1·§1.4의 원문 인용·판정은 이와 **모순 없이 일치**한다(vectorbt·pybroker = 코드 차용 부). vectorbtpro는 §C 표에 별도 항목은 없지만 동일 계열(상업적 이용 시 별도 유상 계약 필요)로, §C의 "BSL 1.1"(상용 사용에 유상 라이선스 필요) 사례와 원리가 같아 같은 근거로 거부한다. zipline-reloaded는 §C 표의 어떤 금지 등급에도 해당하지 않는 순수 Apache-2.0이며, ADR §6이 이미 REFERENCE로 채택했다.

## 4. BT-15 결론 — 실제로 쓸 스택

**BT-15(`backtest/vector/{arrays,signals,fills}.py`)는 numpy 자체 구현으로 착수한다.** 벡터화 신호·체결 배열 연산(불리언 마스크, 누적합, `np.where` 기반 체결가 산정)은 이미 의존성인 `numpy>=2.0`(`pyproject.toml:20`) 하나로 충분하고, BT-2~6(`src/foundation/backtest/domain/fill/`)에 이미 존재하는 순수 함수 체결 모델을 배열 버전으로 재작성하는 작업이다. `numba`는 이번 리프의 의존성에 추가하지 않는다 — BT-15 DoD("이벤트 엔진과 단일 조합 결과 동등성")와 BT-16 DoD("1,000 조합 ≤60s")를 numpy 벡터화만으로 달성하지 못한다고 실측으로 확인되면(BT-16 단계에서), 그때 JIT 도입 여부를 별도 리프·decision으로 재평가한다 — 지금 선제 도입하지 않는다(YAGNI, 이 리프는 평가만 하고 `pyproject.toml`을 건드리지 않는다는 범위 제약과도 부합).

vectorbt·vectorbtpro·pybroker 코드는 벤더링하지 않으므로 BT-15는 그 셋의 API 형태를 참고하되 numpy 배열 연산을 처음부터 작성한다. zipline-reloaded의 슬리피지/수수료 공식은 이미 BT-2~6에 이벤트 기반으로 구현되어 있으므로 BT-15는 그 로직을 배열 형태로 옮기는 작업이며, zipline-reloaded 소스 코드 자체를 다시 참조할 필요는 없다(로직은 이미 BT-2~6에서 확정됨).

## 5. 게이트

이 문서가 BT-14의 전체 산출물이다. 4종 전부 코드 차용은 부(vectorbt·vectorbtpro·pybroker는 라이선스 조건상 금지, zipline-reloaded는 라이선스는 허용하나 ADR §6 결정으로 미사용)이고, 설계 참조는 zipline-reloaded·vectorbt·pybroker 공개 문서 범위에서 가, vectorbtpro는 공개 마케팅 페이지 범위로 제한된다. **BT-15는 numpy 자체 구현으로 착수 가능하다. BT-16/17은 BT-15 완료 후 배정한다.**
