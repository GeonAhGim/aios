# ADR-2026-09-26-E: 지표 플랫폼 통합 — TA-Lib WASM 클라이언트 계산 · OSS 계층 확장 · 검증 폭 확장

## Status
Accepted (2026-09-26). 사용자 방향("talib, pandas 같은 보조지표 모듈들을 아울러서 유연하고 다양하면서도 밀도 높은 차트
성능과 서비스", "버전이 달라져도 유연하게 모두 쓸 수 있도록", 세 축 전부) + 위임 결정("니가 판단해서 결정해") —
Chief Architect(클라우드 세션 aios-a4)가 채택. 구현 리프(CH-20~22, IND-17~21)는 PC 세션 레인에서 명세 §9.11에 편입해
발행한다(클라우드 세션 비용 중단 지시). 순서: D3(검증 일반화)·IND-18(승인 규칙) 선행 → CH-20/21(WASM) → IND-19/20.

## Context (실측 2026-09-26, 근거 파일 병기)
- **CORE 계층(TA-Lib)**: PR #127(`claude/talib-flex-sandbox-spawn`)로 카탈로그가 설치된 TA-Lib 버전에서 도출된다
  (0.7.1=161종, 0.8.1=201종, `generate_specs.py`·`param_rules.py`). 코드에 TA-Lib 버전 리터럴 0. dependabot #118(0.8.1)은
  #127 병합 후 녹색 예상.
- **클라이언트 계산은 201종 중 10종**: `chart-engine/src/compute/clientEngine.ts`는 IND-1 파이썬 증분 엔진의 수기 TS 포트
  10종(3자 교차검증 통과분)만 계산하고 나머지는 서버 왕복(`parityCheck.ts` 폴백). task-1953 결정 문구:
  "no new OSS dependency (no `technicalindicators`, no TA-Lib WASM)". 그러나 ADR-2026-09-06-C D1과 ADR-2026-09-06-F D2,
  명세 CH-18은 **"서버와 같은 C 코드를 쓰는 TA-Lib WASM 빌드를 1순위"**로 적어 두었다 — 1순위가 구현되지 않은 채
  CH-18이 done 처리됐다. 밀도 SLO(ADR-2026-09-06-C D4: 지표 30종×10만 봉 팬/줌 p95 ≤ 16.7ms, 지표 추가 ≤ 100ms,
  틱 ≤ 8ms)는 CH-19가 렌더 측에서 달성했지만, 191종은 팬/줌마다 서버 왕복이라 SLO 밖이다.
- **OSS 계층(pandas-ta-classic)**: 카탈로그 193종, TA-Lib과 이름이 안 겹치는 순증분은 0.7.1 기준 127종 / 0.8.1 기준 96종
  (`pandas_ta_bridge.report_net_incremental_count()`). 로컬 실측(OHLCV 400봉, `pta.<name>(**ohlcv)` 호출):

  | TA-Lib | 순증분 | 단일 출력·OHLCV 입력 | 다중 출력(2~10열) | OHLCV 외 인자 필요·신호 유틸 | 비인과(튜플) |
  |---|---|---|---|---|---|
  | 0.7.1 | 127 | 75 | 35 | 16 | 1(ichimoku) |
  | 0.8.1 | 96 | 55 | 26 | 14 | 1 |

  실제 등록(`pandas_ta_candidates.py`)은 후보 3종(DPO/MASSI/COPPOCK)이고 0.8.1에서는 셋 다 TA-Lib이 제공해 등록 0종.
  즉 OSS 계층은 **거의 비어 있다**(IND-11 DoD "순증분 종수 보고"는 충족, "전량 등록"은 범위 밖이었다).
- **검증 폭**: `reference/verify_all.py` 3자 교차검증(TA-Lib C ↔ 증분 ↔ 벡터)은 두 엔진 커널이 있는 11종만 대상. 나머지
  190종은 TA-Lib C 자체가 유일한 경로라 "검증"의 정의가 없다. 클라이언트 화이트리스트(`verifiedIndicators.ts`)도 이 11종에
  묶여 있어 검증 폭이 곧 클라이언트 계산 폭의 상한이다.
- **라이선스**: TA-Lib C는 BSD-3, 파이썬 래퍼는 BSD-2(`INDICATOR_OSS_EVAL.md` §2.1, 고지 2건 동봉 의무). pandas-ta-classic
  MIT(§2.3, `oracle` extra 금지). GPL/LGPL(tulip·backtrader·nautilus) 반입 금지(§5).
- **TA-Lib 공식 JS/WASM 바인딩은 없다**(2026-09-26 GitHub org 열람: ta-lib, ta-lib-python, -zig, -cgo, ext-ta-lib(PHP),
  ta_pg, -ruby만 존재). C 0.8.1 빌드 시스템은 autotools·CMake 두 가지(릴리스 노트 원문 "autotools and CMake are the only
  two supported build systems"). 따라서 WASM은 **우리가 Emscripten으로 빌드해 vendor 반입**해야 한다(CH-1 KLineChart 포크
  반입과 같은 패턴). ta-lib.org 문서는 이 컨테이너에서 egress 차단으로 열람 불가 — §7 미확인 항목에 남긴다.
- **가드 충돌**: aios-meta `architecture_guard` P6는 변경된 `src/*.py` 300줄 초과를 veto한다(고정 커밋 2026-09-08,
  allow 마커 없음). ADR-2026-09-10-C가 300줄 규칙을 폐지했는데 CI 가드는 그대로다(#127에서 두 파일 분할로 대응).
  아래 리프들은 파일이 늘어나므로 이 충돌을 먼저 정리해야 한다(§6 Open questions).

## Decision

### D1. 클라이언트 지표 엔진을 TA-Lib WASM으로 교체한다 — "서버와 같은 C 코드" 원칙 이행
- `frontend/packages/chart-engine/vendor/talib-wasm/`에 TA-Lib C(pyproject의 TA-Lib 래퍼가 요구하는 **동일 C 버전**)를
  Emscripten으로 빌드한 `.wasm` + 얇은 TS 바인딩을 반입한다. 빌드 스크립트(`scripts/build_talib_wasm.sh`)·소스 커밋 SHA·
  산출물 sha256을 함께 커밋해 재현 가능하게 한다(빌드 도구체인은 CI 의존이 아니라 산출물 검증만 CI에서 한다).
- **버전 핸드셰이크**: 서버 `GET /v1/indicators`(IND-12) 응답에 `talib_version`(C 라이브러리 버전)을 실어 주고, 클라이언트는
  WASM 바이너리의 `TA_GetVersionString()`과 다르면 **그 세션의 클라이언트 계산을 전부 끄고 서버 폴백**한다(무음 금지,
  `parityCheck.ts` `onMismatch` 경로 재사용). 이것이 #127의 "버전 리터럴 0" 원칙의 클라이언트 측 대응이다.
- 화이트리스트 게이트(`verifiedIndicators.ts`)의 의미를 "수기 포트가 검증된 10종"에서 **"WASM 버전 일치 + D3 패리티 샘플
  통과"**로 바꾼다. 전 201종이 대상이 되되, 게이트는 여전히 fail-closed(패리티 미확인 = 서버 값 표시).
- 기존 수기 TS 커널 10종(`kernelFactories.ts`)은 WASM 패리티가 nightly에서 연속 통과(§5 조건)한 뒤 **삭제**한다 —
  같은 지표에 계산 경로 3개(C·WASM·TS)를 두지 않는다(ADR-2026-09-06-F).
- OSS 계층(pandas-ta)은 WASM 대상이 아니다 — 파이썬 전용이므로 **서버 계산 유지**. 밀도 SLO는 CORE 201종에서 달성한다.

### D2. OSS 계층 후보 승인 규칙을 코드로 고정하고 순증분을 단계적으로 등록한다
- 승인 규칙(`pandas_ta_candidates.py`에 선언, 테스트가 전 후보에 강제): (a) 입력이 OHLCV 열만 (b) 인과적(미래 봉을 바꿔도
  과거 출력 불변 — 이미 있는 DPO 테스트 방식) (c) 단일 출력이거나, 다중 출력이면 각 열에 `PlotSpec`(IND-15)이 붙는다
  (d) 설치된 TA-Lib과 이름 기반 비중복(`select_registered`, #127) (e) 독립 재구현 또는 TA-Lib 오라클과의 교차검증 1건.
- 1차 배치: 단일 출력·OHLCV 후보(0.8.1 기준 55종). 2차: 다중 출력 26종 중 `PlotSpec` 매핑이 자명한 밴드·채널류
  (donchian·kc·supertrend·squeeze 등). 제외: OHLCV 외 인자·신호 유틸 14종(tsignals/xsignals/long_run/…)과 비인과 ichimoku
  (forward shift; `series_cache.build()`의 `causal=True` 계약과 충돌 — `causal=False` 표기로 차트 표시만 허용할지는 별도 결정).
- 후보 조사는 손으로 하지 않는다: `scripts/survey_pandas_ta_candidates.py`가 설치 버전 기준으로 위 표를 재생성하고, 등록
  집합과의 차이를 리포트한다(TA-Lib 버전이 바뀌어 순증분이 줄어들면 자동으로 `SUPERSEDED_BY_TALIB`로 이동).

### D3. 검증을 "3자 11종"에서 "가용 오라클 전부 × 전 종"으로 일반화한다
- `verify_all.py`를 오라클 조합 기반으로 바꾼다: 지표마다 사용 가능한 구현 집합 {TA-Lib C(서버), TA-Lib WASM(클라이언트,
  D1), 증분 엔진, 벡터 엔진, 독립 재구현(pandas-ta 후보)} 중 2개 이상이 있으면 검증 대상. 허용오차는 기존
  `REFERENCE_TOLERANCE`(1e-9, 스케일 상대) 하나만 쓴다 — 지표별 완화 금지.
- WASM 패리티(TA-Lib C ↔ WASM)는 같은 C 코드라 원칙상 비트 동일이어야 하며, 차이가 나면 빌드 결함(컴파일러 플래그·
  libm 차이)이다 — 이 검증이 D1 반입의 게이트다. 산출 참조 벡터는 `reference/vectors/`에 스냅샷으로 남겨 프론트
  `fixtures/referenceVectors.ts`가 그대로 이식한다(현행 구조 유지).
- 기대 상태(어느 지표가 제외되는가)는 버전 리터럴이 아니라 오라클에서 도출한다(#127의 BBANDS 방식). nightly 전수,
  CI는 샘플(IND-13 구조 유지).

### D4. 버전 유연성 원칙(#127)을 플랫폼 불변식으로 승격
- 카탈로그 종수·MA 타입 상한·후보 등록 여부·기대 제외 집합은 전부 **설치된 라이브러리에서 도출**한다. 코드·테스트에
  TA-Lib/pandas-ta 버전 리터럴을 두지 않는다(허용: 의존성 핀 파일, 이 ADR 같은 문서). 정적 가드 후보:
  `scripts/check_indicator_version_literals.py`(warn+baseline으로 도입, CLAUDE.md §6-6).

## Consequences (리프 분해 초안 — 결정 후 명세 §9.11에 편입)
| ID(안) | 내용 | 선행 | DoD | 예상 |
|---|---|---|---|---|
| CH-20 | TA-Lib C WASM 빌드 스크립트 + vendor 반입(sha256·NOTICE BSD-3) | — | 재현 빌드 해시 일치, 번들 크기 실측 기록 | 300 |
| CH-21 | `compute/talibWasm.ts` 바인딩 + 워커 통합 + 버전 핸드셰이크 | CH-20 | 버전 불일치 시 전 지표 서버 폴백(negative) | 400 |
| IND-17 | `verify_all` 오라클 조합 일반화 + WASM 패리티 nightly | CH-21 | 201종 C↔WASM 패리티 0 불일치, 제외 집합 오라클 도출 | 350 |
| CH-22 | 화이트리스트 게이트 재정의 + TS 커널 10종 제거 | IND-17 연속 통과 | 계산 경로 2개(C·WASM)만 남음 | 200 |
| IND-18 | OSS 후보 승인 규칙 코드화 + 조사 스크립트 | — | 규칙 위반 후보 등록 시 테스트 적색 | 250 |
| IND-19 | OSS 1차 배치(단일 출력 55종) 등록 + 교차검증 | IND-18 | 종별 negative·인과성·오라클 1건 | 600 |
| IND-20 | OSS 2차 배치(다중 출력 밴드·채널류) + PlotSpec 매핑 | IND-19, IND-15 | 화면 코드 무변경으로 채움 렌더 | 400 |
| IND-21 | 버전 리터럴 정적 가드(warn+baseline) | — | 리터럴 도입 시 warn | 120 |

- 의존성 결정(CLAUDE.md §6-8): Emscripten 도구체인(빌드 시점만), vendor에 TA-Lib C 소스 스냅샷(BSD-3 고지
  `THIRD_PARTY_NOTICES.md` 추가). 런타임 신규 npm 의존성 0.
- 되돌림: WASM 패리티가 IND-17에서 0 불일치를 못 내면 CH-22를 진행하지 않고 TS 커널 10종·서버 폴백 현행 유지.

## Rejected
- `technicalindicators`(MIT) 채택: JS 독자 구현이라 TA-Lib과 수치 레시피(EMA 시드·Wilder·0 나눗셈)가 달라 1e-9 패리티를
  통과할 근거가 없다. 검증 실패분은 어차피 서버 폴백이 되어 밀도 개선이 없다.
- 191종 수기 TS 포팅 계속: 종당 검증 비용이 선형으로 늘고, TA-Lib 버전이 바뀔 때마다(0.8.1처럼 BBANDS·APO/PPO 기본값 변경)
  포트가 조용히 드리프트한다.
- pandas-ta를 브라우저에서 실행(pyodide): 번들 수십 MB, 시작 수 초 — 밀도 SLO와 정면 충돌.
- 클라이언트 계산 전면 폐지(서버만): ADR-2026-09-06-C Rejected 그대로.

## Open questions → 결정(2026-09-26)
1. **aios-meta P6 300줄 가드 vs ADR-2026-09-10-C** → 결정: ADR이 정본. 사용자 결정 "300줄 제한 폐지"로 aios-meta PR #2(43f9f94a) 병합, 500 flag·800 flag·1,000 veto(loc-allow 예외). META_GUARDS_REF 갱신은 PC 세션 위임. (원 질문: 어느 쪽이 정본인가. 가드가 정본이면 위 리프들은 파일 분할 비용을
   예산에 포함해야 하고, ADR이 정본이면 aios-meta 고정 커밋을 갱신해야 한다(사람 전용 저장소).
2. WASM 산출물 → 결정: 저장소에 커밋 + sha256 재현 검증(CI에 Emscripten 불필요). 빌드 스크립트·소스 SHA 동봉.
3. ichimoku류 비인과 지표 → 결정: 카탈로그에서 제외(fail-closed). `causal=False` 표시 전용 계약은 PlotSpec/series_cache 계약 설계가 있는 별도 ADR에서만 재개.
4. 1차 OSS 배치 → 결정: 승인 규칙을 통과하는 단일 출력 후보 전부(0.8.1 기준 55종)를 대상으로 하되, 리프는 카테고리별로 나눠 발행(momentum·overlap·trend·volume·volatility·statistics). 사용 데이터가 없어 빈도 우선순위는 두지 않는다.

## 미확인 항목 (추정하지 않음)
- ta-lib.org 스트리밍 API 문서·WASM 관련 안내(컨테이너 egress 차단으로 미열람).
- TA-Lib C 0.8.1 Emscripten 빌드 가능성·번들 크기·libm 수치 동일성 — CH-20에서 실측으로만 확정한다.
- TA-Lib 0.8.1의 BBANDS 기본 timeperiod 5→20, APO/PPO matype→EMA 변경이 사용자 저장 차트 템플릿(CH-17)에 미치는 영향 —
  현재 수기 오버라이드가 BBANDS 기본값 5를 유지하지만 APO/PPO는 자동 생성이라 기본값이 바뀐다. 별도 확인 필요.

## 관련
ADR-2026-09-05-A(OSS 어댑터 층), ADR-2026-09-06-C(고밀도 차트), ADR-2026-09-06-F(adopt-don't-rewrite), ADR-2026-09-09-A
(pandas-ta-classic 대체), ADR-2026-09-10-C(파일 정책), INDICATOR_OSS_EVAL.md, PR #127.
