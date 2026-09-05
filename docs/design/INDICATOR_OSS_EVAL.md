# INDICATOR_OSS_EVAL — 지표 OSS 라이브러리 반입 평가 (IND-9)

- 리프: IND-9 (`docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md` §9.9)
- 근거: ADR-2026-09-05-A D1(OSS 어댑터 층, GPL/LGPL 코드 차용·링크 금지), Consequences("라이선스 검증 리프 통과 전 어떤 OSS도 반입하지 않는다")
- 형식: CH-0(`CHART_ENGINE_FORK_EVAL.md`, b0826da) 채점표 형식 재사용
- 범위: **문서만.** 코드 변경 0, 의존성 추가 0. IND-10(TA-Lib 브리지)·IND-11(pandas-ta 브리지)은 이 문서를 CA가 확정하기 전에 배정하지 않는다.
- 확인일: 2026-09-06. 모든 라이선스 원문은 PyPI 배포물(wheel/sdist 내부 파일) 또는 각 저장소 `raw.githubusercontent.com`에서 직접 읽었다. 확인하지 못한 항목은 "미확인"으로 남기고 추정하지 않았다(§7).

## 0. 채점 원칙 — 전제와 게이트

**전제(사실 확인).** AIOS 저장소는 public 모노레포(ADR-2026-09-03-A)이지만 루트에 `LICENSE` 파일이 없다(OSS 라이선스 미선언 = 저작권 유보). 제품은 테넌트 인증 뒤의 유료 SaaS이고, 마켓(MP)은 "보호 소스" 판매 모델을 가진다. 따라서 **AIOS 전체를 카피레프트로 전환시키는 라이선스는 종수·품질과 무관하게 탈락**이며, 사유는 조항 번호 + 원문으로 남긴다(§5).

**이미 반입된 것.** `pyproject.toml`에 `TA-Lib>=0.6`이 선언되어 있고 `src/core/indicators/{talib_adapter,specs_talib}.py`가 11종을 쓰고 있다(L01~L03, 2026-08). 즉 TA-Lib은 사후 평가다 — 이 문서가 그 반입을 소급 정당화하거나 철회하는 근거가 된다.

## 1. 채점 축과 가중치

| 축 | 가중치 | 근거 |
|---|---|---|
| ① 라이선스 자유도·의무 무게 | 40 | ADR D1 최상위 제약. 게이트 통과 후에도 고지·비보증·특허 조항 부담을 세분화 |
| ② 품질: 참조 벡터 가용·스펙 자동 생성·증분 계약 | 25 | IND-10/11 DoD("참조 벡터 검증", "증분=일괄 동일성")를 직접 좌우 |
| ③ 종수·커버리지 | 20 | IND-10 ≥150종, IND-11 ≥100종 DoD |
| ④ 유지보수·의존성 무게·런타임 호환 | 15 | 현 venv는 Python 3.10.11 / numpy 2.2.6 — 설치 불가면 반입 불가 |

## 2. 라이선스 원문 확인 (조항 번호 + 원문 인용)

### 2.1 TA-Lib — C 라이브러리(`ta-lib/ta-lib` v0.7.1) + Python 래퍼(`TA-Lib` 0.7.1, `ta-lib/ta-lib-python`)
- **두 라이선스가 겹친다.** 래퍼 wheel의 `ta_lib-0.7.1.dist-info/licenses/LICENSE`는 **BSD 2-Clause**("# BSD 2-Clause License / Redistribution and use in source and binary forms, with or without modification, are permitted provided that... 1. Redistributions of source code must retain the above copyright notice... 2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution."). GitHub `licensee`도 BSD-2-Clause.
- C 라이브러리 `LICENSE`(main)는 **BSD 3-Clause**: "Copyright (c) 1999-2026, Mario Fortier" + 위 1·2항 + "3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission." GitHub `licensee`: BSD-3-Clause. ADR D1의 "BSD-3" 표기는 C 라이브러리 기준으로 맞다.
- **바이너리 번들 사실.** 래퍼 README §Wheels: "starting with version 0.6.5, we now build binary wheels... which include the underlying TA-Lib C library". 로컬 wheel RECORD에는 `talib/_ta_lib.cp310-win_amd64.pyd`(1,605,632 B) 하나뿐이고 C 라이브러리 LICENSE 원문은 **동봉되어 있지 않다**(`talib.__ta_version__` = `0.7.1 (Jul 16 2026)`로 C 코드가 정적 포함됨은 확인).
- 결론: 재배포·수정 자유. 의무는 (a) AIOS가 배포하는 바이너리/문서에 **BSD-2(래퍼)와 BSD-3(C) 두 고지를 직접 실어야 한다**(wheel이 C 고지를 빠뜨리므로 AIOS가 보완) (b) "TA-Lib"·Mario Fortier 이름을 홍보에 쓰지 않는다(3항). NOTICE 전파·귀속 링크 의무 없음. **의무 최소, 단 고지 파일은 IND-10에서 추가 필요.**

### 2.2 pandas-ta (`pandas-ta` 0.4.71b0, PyPI 2025-09-14)
- wheel·sdist 모두 `LICENSE` 포함: "The MIT License (MIT) / Copyright (c) 2019+ Kevin Johnson / Permission is hereby granted, free of charge, to any person obtaining a copy of this software... to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies..." → 배포물 기준 **MIT 확정**.
- **출처 상태(사실).** PyPI `Repository` URL `github.com/twopirllc/pandas-ta`는 GitHub API가 `Not Found`(404)를 반환한다(비공개 전환인지 삭제인지는 미확인). PyPI 릴리스 이력은 **0.4.67b0(2025-09-03)·0.4.71b0 두 건만** 남아 있고 과거 0.3.x 계열은 목록에 없다. PyPI 설명문의 라이선스 링크(`pandas-ta.dev/legal/license/`)는 연결 시간 초과, `www.pandas-ta.dev`는 DNS 해석 실패로 **미확인**.
- 결론: 라이선스 자체는 MIT로 통과. 그러나 소스 이력·이슈·CLA를 열람할 공개 저장소가 없어 provenance 감사가 불가능하고, 배포자가 임의로 이력을 지운 전례가 있다. 라이선스 축 감점 사유.

### 2.3 pandas-ta-classic (`pandas-ta-classic` 0.6.52, `xgboosted/pandas-ta-classic`) — IND-11 대체 후보
- 명세에는 없으나 §2.2 상황 때문에 평가에 추가했다. wheel `LICENSE`: "The MIT License (MIT) / Copyright (c) 2021+ pandas-ta contributors / Copyright (c) 2024+ pandas-ta-classic contributors (xgboosted/pandas-ta-classic)" + 표준 MIT 본문. GitHub `licensee`: MIT. PyPI `License: MIT`.
- 원저작(pandas-ta 0.3.x 계보) 귀속 줄을 유지하고 있어 MIT "above copyright notice... shall be included" 조건을 포크가 이행 중임을 원문으로 확인.
- 주의: extras `oracle`이 `ta-lib`·`tulipy`(LGPL-3.0)를 **테스트 오라클**로 끌어온다. AIOS는 `oracle` extra를 절대 설치하지 않는다(§5). 기본 의존성은 `numpy>=2.0.0, pandas>=2.0.0`뿐.

### 2.4 ta (`ta` 0.11.0, `bukosabino/ta`)
- 저장소 `LICENSE`: "The MIT License (MIT) / Copyright 2020 Darío López Padial (Bukosabino)" + 표준 MIT 본문. PyPI `License: The MIT License (MIT)`, classifier `OSI Approved :: MIT License`.
- **sdist(`ta-0.11.0.tar.gz`)에는 LICENSE 파일이 없다**(메타데이터 문자열만). 반입 시 저장소에서 원문을 가져와 동봉해야 MIT 고지 조건을 충족한다. 사소하지만 실재하는 패키징 결함.

## 3. 품질 — 참조 벡터·스펙 자동 생성·증분 계약 (전부 로컬 실측 또는 원문 확인)

| 후보 | 참조 벡터 | 스펙 자동 생성 | 증분=일괄 동일성 |
|---|---|---|---|
| TA-Lib | C 저장소 `src/tools/ta_regtest/`에 회귀 테스트 45개 파일(`ta_test_func/test_*.c`) + `ta_test_reference_golden.{c,h}`("Produced by scripts/gen_test_reference.py... **in exact rational arithmetic**", NIST 인증값 대조). 래퍼는 `tests/test_{func,abstract,stream,pandas,polars}.py` | `talib.abstract.Function(name).info`가 161/161 함수에서 `parameters`(기본값)·`input_names`·`output_names`·`output_flags`·`group`을 제공, `.lookback` 실측 가능(MACD=33). 파라미터 **범위(min/max)는 미제공** → 범위는 AIOS가 정의 | **`talib.stream`은 일괄과 불일치**(실측: 600봉 RSI(14) 일괄 69.2036 vs stream 64.9288, `set_unstable_period('RSI',500)` 후에도 동일; stream.EMA(20)=135.6985는 SMA 시드값과 같음). 동일성은 IND-1 엔진의 warm-up 창 재계산으로 구현해야 하며 stream API를 쓰면 안 된다 |
| pandas-ta 0.4.71b0 | sdist `tests/` 15개 + `data/{ADX_D,ALT_D,TEST_D,sample}.csv` 동봉. 테스트 9개가 TA-Lib을 오라클로 대조(`test_indicator_{momentum,overlap,statistics,candle}.py` 등). 저장소 열람 불가 → 테스트 통과 여부는 로컬 재실행으로만 확인 가능(Python 3.12 필요, 미실행) | `pandas_ta/maps.py` `Category` 딕셔너리(161 항목)로 카테고리는 자동, 파라미터 범위·lookback 메타는 없음(함수 시그니처 기본값뿐) | 전부 일괄(DataFrame) 계산. numba JIT 커널 사용 |
| pandas-ta-classic 0.6.52 | 저장소 `tests/` 43개 파일: `test_oracle_talib.py`, `test_property_based.py`(hypothesis), `test_nan_behaviour.py`, `test_regression*.py`, `test_indicator_values.py`. `test_oracle_tulipy.py`는 optional extra | pandas-ta와 동일 구조(카테고리 자동, 범위·lookback 메타 없음) | 일괄 계산. numba는 `performance` extra로 선택 |
| ta 0.11.0 | 저장소 `test/data/cs-*.csv` 31개(출처 주석: `school.stockcharts.com` 지표별 스프레드시트) + `datas.csv`. **sdist에는 미포함.** 테스트 주석이 RSI 초기화가 stockcharts와 다름(`ewm` 직접 사용)을 자인 | 클래스 43 + 함수 80, 메타 없음 | 일괄 계산(pandas) |

## 4. 종수·유지보수·의존성 무게 (실측: pip/PyPI JSON/GitHub API, 2026-09-06)

| 후보 | 종수(실측) | 최신 배포 | 저장소 최근 push / ★ / archived | Python·의존성 |
|---|---|---|---|---|
| TA-Lib 0.7.1 | `talib.get_functions()` **161** = Momentum 31·Overlap 18·Volatility 3·Volume 3·Cycle 5·Price Transform 5·Statistic 9·**Pattern Recognition 61**·Math Operators 11·Math Transform 15 (파라미터 없는 함수 87) | 2026-07-16 | ta-lib-python 2026-08-29 / 12,232 / false; ta-lib C 2026-09-05 / 1,664 / false | ≥3.9, `numpy` + `build`. cp310 win wheel 존재(로컬 설치 확인) |
| pandas-ta 0.4.71b0 | `Category` 161 항목 / 모듈 157(momentum 44·overlap 36·trend 21·volume 20·volatility 16·statistics 10·candle 5·performance 3·cycle 2) | 2025-09-14 | **404** / 미확인 / 미확인 | **`>=3.12`**(현 venv 3.10 → 설치 거부, `pip download` 실측), `numba==0.61.2` 고정(llvmlite 동반, 2.8 MB) + `numpy>=2.2.6` + `pandas>=2.3.2` + `tqdm` |
| pandas-ta-classic 0.6.52 | 모듈 **255**(candles 66·momentum 53·overlap 46·trend 26·volume 20·volatility 18·statistics 14·cycles 9·performance 3) | 2026-06-24 | 2026-07-25 / 427 / false | `>=3.10`, `numpy>=2.0`·`pandas>=2.0`만. numba·scipy·tulipy는 extras |
| ta 0.11.0 | 클래스 **43**(README "43 indicators"와 일치), 편의 함수 80 | 2023-11-02 | 2026-03-18 / 5,184 / false | 버전 제한 없음, `numpy`·`pandas`. sdist만 배포(wheel 없음) |

IND-10 DoD "≥150종"은 캔들 패턴 61종과 Math 계열 26종을 포함해야만 달성된다(고전 지표만 세면 74). 등록은 하되 카테고리를 분리 노출해야 한다.

## 5. GPL/LGPL 제외 근거 — tulip · backtrader · NautilusTrader

| 후보 | 라이선스(원문 확인) | 상태 |
|---|---|---|
| tulipindicators(C, `TulipCharts/tulipindicators`) / tulipy 0.4.0 | **LGPL-3.0** — 저장소 LICENSE 원문 "GNU LESSER GENERAL PUBLIC LICENSE Version 3"; PyPI `License: LGPL-3.0` | tulipy 마지막 push 2019-04-11(★93), C 라이브러리 2024-02-02(★942) |
| backtrader 1.9.78.123 | **GPL-3.0** — LICENSE "GNU GENERAL PUBLIC LICENSE Version 3"; PyPI `GPLv3+` | 마지막 push 2024-08-19(★23,146), 마지막 배포 2023-04-19 |
| nautilus_trader 1.231.0 | **LGPL-3.0-or-later** — LICENSE "GNU LESSER GENERAL PUBLIC LICENSE Version 3"; PyPI classifier LGPLv3+ | 활발(2026-09-05 push, ★28,401) |

제외 사유(조항 인용):
- GPL-3.0 §5(c): "You must license the entire work, as a whole, under this License to anyone who comes into possession of a copy... regardless of how they are packaged." AIOS는 public 저장소로 소스를 전달(convey)하므로 backtrader 코드를 한 파일이라도 차용하면 §5(c)가 저장소 전체에 발동한다. 라이선스 미선언·보호 소스 마켓 모델과 양립 불가 → 활성도와 무관하게 **탈락**.
- LGPL-3.0 §4(Combined Works): "You may convey a Combined Work under terms of your choice... if you also do each of the following: a) Give prominent notice with each copy of the Combined Work that the Library is used in it and that the Library and its use are covered by this License. b) Accompany the Combined Work with a copy of the GNU GPL and this license..." 게다가 IND-10/11 방식(코드를 어댑터로 흡수·수정)은 §4가 아니라 §2(Modified Versions)에 해당해 수정본 전체가 LGPL이 된다. Python `import`가 §4의 "Combined Work" 경계에 해당하는지도 법적으로 확정된 바 없다. 따라서 tulip·Nautilus는 코드 차용·벤더링·수정 금지, ADR D1대로 **설계 패턴 참조만** 허용.
- 실무 규칙: `tulipy`는 pandas-ta-classic `oracle` extra로 유입될 수 있다. `pyproject.toml`·IND-13 nightly 환경 어디에도 `tulipy`가 설치되면 안 되며, IND-10/11 QA는 `pip show tulipy`가 "not found"임을 확인한다.

## 6. 채점표 및 결론

| 후보 | ① 라이선스(×4) | ② 품질(×2.5) | ③ 종수(×2) | ④ 유지·의존(×1.5) | 총점(100) |
|---|---|---|---|---|---|
| **TA-Lib 0.7.1** | 9 → 36 | 8 → 20 | 9 → 18 | 9 → 13.5 | **87.5** |
| **pandas-ta-classic 0.6.52** | 9 → 36 | 8 → 20 | 10 → 20 | 8 → 12 | **88** |
| pandas-ta 0.4.71b0 | 8 → 32 | 6 → 15 | 9 → 18 | 2 → 3 | 68 |
| ta 0.11.0 | 8 → 32 | 6 → 15 | 3 → 6 | 5 → 7.5 | 60.5 |
| tulip / backtrader / Nautilus | 게이트 탈락(§5) | — | — | — | 0 |

산정 근거: ①은 §2·§5(고지 의무 무게·provenance·카피레프트). ②는 §3(참조 벡터 형태·스펙 메타·증분 계약 실측). ③은 §4 실측 종수 대 DoD. ④는 §4(런타임 호환·핀 고정·활성도). TA-Lib ②가 10이 아닌 이유는 stream 불일치와 파라미터 범위 부재, pandas-ta-classic ④가 10이 아닌 이유는 커뮤니티 규모(★427)와 포크 역사가 짧다는 점.

### IND-10 (TA-Lib 브리지): **반입 가**
- 이미 반입되어 있으며 라이선스 원문(BSD-2 + BSD-3)이 재배포·수정을 허용한다. 착수 조건: (1) `docs/design/THIRD_PARTY_NOTICES.md` 또는 동등 파일에 BSD-2(래퍼)·BSD-3(C, Mario Fortier) 원문을 동봉 (2) 증분=일괄 동일성은 `talib.stream`이 아니라 warm-up 창 재계산으로 구현하고 §3 실측 사례(RSI·EMA)를 negative test로 고정 (3) 파라미터 범위는 AIOS 스펙이 소유(`specs_talib.py` 방식 확장) (4) 캔들 패턴 61종·Math 26종은 별도 카테고리로 노출.

### IND-11 (pandas-ta 브리지): **명세 원안(pandas-ta 원본) 불가, 대체안 조건부 가**
- 원본 `pandas-ta` 0.4.71b0은 Python ≥3.12 요구로 현 런타임에 **설치 자체가 안 되고**, 공개 저장소가 없어 코드 감사·패턴 참조가 불가하며, numba 정확 버전 고정이 AIOS의 numpy/numba 정책과 충돌할 여지가 있다. 라이선스(MIT)는 문제가 아니지만 ④축이 결정적.
- 대체안 `pandas-ta-classic` 0.6.52는 MIT·공개 저장소·Python 3.10·경량 의존성·TA-Lib 오라클 테스트를 모두 갖추고 255 모듈로 IND-11 DoD(≥100종)를 여유 있게 넘긴다. **명세 §9.9 IND-11의 대상 패키지를 `pandas-ta-classic`으로 바꾸는 것은 CA 결정 사항**이며, 결정 전에는 IND-11을 배정하지 않는다. 배정 시 `oracle` extra 미설치가 DoD에 포함되어야 한다.

### ta: **브리지 불필요**
- 43종은 TA-Lib·pandas-ta-classic과 거의 전부 중복되고 별도 층을 만들 가치가 없다. 단, `test/data/cs-*.csv` 31개(MIT, stockcharts 출처)는 IND-13 교차검증의 **제3 참조 벡터**로 가져올 수 있다(저장소 LICENSE 원문 동봉 조건).

## 7. 미확인 항목 (추정하지 않음)
- `twopirllc/pandas-ta` 저장소 404의 사유(비공개 전환 vs 삭제), 0.3.x 릴리스가 PyPI 목록에서 사라진 경위.
- `pandas-ta.dev/legal/license/` 페이지 내용(연결 시간 초과) — 배포물 내 LICENSE 파일로 대신 확인.
- pandas-ta 0.4.71b0 sdist 테스트의 실제 통과 여부(Python 3.12 환경 없음, 미실행).
- TA-Lib C `ta_regtest` 골든 값의 실행 결과(빌드 미수행; 파일 존재와 생성 방식만 원문 확인).

### 게이트
이 문서가 IND-9의 전체 산출물이다. **IND-10은 CA가 이 채점표를 승인한 뒤 착수하고, IND-11은 대상 패키지(pandas-ta → pandas-ta-classic) 변경 여부를 CA가 결정한 뒤 배정한다.** GPL/LGPL 3종은 어떤 형태로도 코드가 저장소에 들어오지 않는다.
