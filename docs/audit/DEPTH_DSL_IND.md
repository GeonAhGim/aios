# DEPTH 감사 — DSL/IND done 리프 D0~D3 판정 (ADR-2026-09-09-C)

- 감사일: 2026-09-10 (task-2727, qa-4)
- 대상: `pm/tasks`의 done implement task 중 제목이 `/DSL-|IND-/`에 맞는 리프 전수(29건, 60건 이하이므로 전수 대조).
- 축 하한: DSL(스크립트 DSL)·IND(지표)는 ADR-2026-09-09-C의 안전축 목록(R, L4, LA/LB/LC, FA, CM, EO, DC)에 없다 → **D2 하한**(안전축 D3 적용 대상 아님).
- 판정 기준(ADR-2026-09-09-C): D0=스텁, D1=happy path+단위 테스트, D2=경계·실패·동시성·복구 테스트 **전부** 충족(negative≥3, 실패 주입 1, 성능 단언 1, 게이트 적색 재현 1), D3=D2 + 적대적/리플레이/다중 인스턴스 증명.
- 방법: 각 리프의 커밋(`git show <commit> --stat`)과 테스트 파일을 직접 열어 `def test_`/`it(` 개수, `pytest.raises`/negative 라벨, `monkeypatch`/`MagicMock` 기반 실패 주입, `perf_counter`/수치 단언, 게이트(exit code) 재현 테스트 유무를 실측했다. 코드 수정 없음.

## 요약

- 전체 29건 중 **29건 전부 D2 미달**(meets_floor=false 29/29).
- 등급 분포: D0 2건(2331, 2658 — 실질 코드 없는 취소된 중복 발행) · D1 27건 · D2 0건 · D3 0건.
- 가장 흔한 단일 누락 항목: **수치 성능 단언 부재**(29/29 전부 — 있는 곳도 전부 "print만, 단언 금지"로 명시적으로 회피, 예: DSL-12 컴파일 300ms 예산·cond_v2 브리지 지연·scripts_router elapsed_ms). 다음으로 **CI 게이트 적색 재현 테스트 부재**(27/27 실질 리프, IND-7g·IND-13만 예외).
- 가장 근접한 리프(3/4 충족, perf만 누락): **IND-7g(task-1738)**와 **IND-13(task-2326)** — 둘 다 `monkeypatch`로 참조 엔진 간 값을 실제로 어긋나게 만들고(실패 주입) `main()`/`verify_indicators_nightly.main()`이 nonzero exit로 반응함을 단언하는 진짜 게이트 적색 재현 테스트를 보유한다. 나머지 25건은 순수 도메인 검증(fail-closed 거부)뿐이라 실패 주입·게이트 재현이 전무하다.
- DSL-11(task-2182, script_facade.py)은 FROZEN 전략 엔진 파사드로 실행(L4) 인접 리프지만 제목이 `DSL-`이라 이 감사는 D2 축으로 판정한다 — negative 2건뿐(<3)이라 D2 최소조건인 negative≥3조차 미달, 가장 얕은 리프 중 하나다.
- IND-9(task-1536)는 순수 문서 산출물(INDICATOR_OSS_EVAL.md, 코드/테스트 0)이라 증빙 자체가 불가능 — D0.
- 2331(IND-13 중복, "[중복 취소]")과 2658(IND-11 중복, "2720과 중복 발행 — 취소(코드 없음)")은 코드·테스트 변경이 전혀 없는 취소된 중복 발행 done 마킹이다. 실질 리프는 각각 2326(IND-13, 이 표에 포함되어 D1 판정됨)과 2720(IND-11, 아직 assigned·미완료라 이 감사 범위 밖)이 대신한다.

## 전수 판정표

| id | title | commit | axis | grade | floor met | evidence | missing (D2 미달 근거) |
|---|---|---|---|---|---|---|---|
| 1234 | DSL-1 grammar/ast.py 불변 AST + 직렬화 왕복 | b6182bb | D2 | D1 | **NO** | test_ast.py: 11 tests, negative=8(음수 인덱스·extra=forbid 등 거부); to_dict/from_dict 왕복 항등 단언 | 실패 주입 없음, 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 적대적 증명 없음 |
| 1235 | DSL-2 grammar/lexer.py 토큰 전 종류 + 오류 위치 | eb905d8 | D2 | D1 | **NO** | test_lexer.py: 17 tests, negative=3(잘못된 문자·미종결 숫자 리터럴 위치 포함 거부) | 실패 주입 없음, 성능 단언 없음, 게이트 적색 재현 없음, D3 증명 없음 |
| 1337 | DSL-3 grammar/parser.py §3.3 문법 전 규칙 | 5812ee4 | D2 | D1 | **NO** | test_parser.py: 40 tests, negative=9(문법표 밖 구문 전부 SCRIPT_SYNTAX 거부) | 실패 주입 없음, 성능 단언 없음, 게이트 적색 재현 없음, D3 증명 없음 |
| 1354 | DSL-4 typing/types.py + typing/checker.py 정적 타입 검사 | 090bba0 | D2 | D1 | **NO** | test_checker.py: 31 tests, negative=11(타입 격자 위반 거부) | 실패 주입 없음, 성능 단언 없음, 게이트 적색 재현 없음, D3 증명 없음 |
| 1369 | DSL-5 analysis/lookahead.py 미래참조 정적 검출(fail-closed) | 0c2c8d9 | D2 | D1 | **NO** | test_lookahead.py: 10 tests, negative=6(변수/음수 인덱스·security() 네임스페이스 거부) | 실패 주입 없음, 성능 단언 없음, 게이트 적색 재현 없음, D3 증명 없음 |
| 1502 | DSL-6 analysis/resources.py 자원 산정치 상한 거부 | c2d1841 | D2 | D1 | **NO** | test_resources.py: 21 tests, negative=6(상한 초과·산정 불가 6종 거부) | 실패 주입 없음, 수치 성능 단언 없음(런타임 자원 상한만 검사, 산정 자체의 속도 단언 없음), 게이트 적색 재현 없음, D3 증명 없음 |
| 1503 | DSL-7 ir/ops.py + ir/lower.py AST→IR 로우어링(결정론) | db7018f | D2 | D1 | **NO** | test_lower.py: 22 tests, negative=11(타입 오류 전파·env 불일치·미지원 노드·inf 상수·스택 언더플로/잔여·버전 거부 등 7종); 같은 소스 2회·AST dict 키 역순·PYTHONHASHSEED 상이 프로세스 3겹 바이트 동일성 단언(D3 리플레이에 준하나 단일 코드경로·비적대적) | 실패 주입 없음, 수치 성능 단언 없음, 게이트 적색 재현 없음; 결정론 증명이 3프로세스뿐이라 D3 다중 인스턴스 요건에는 못 미침 |
| 1505 | IND-1 engine/{incremental,vectorized}.py 증분=일괄 1e-9 동일성 | e7b99b1 | D2 | D1 | **NO** | test_engine_equivalence.py: 12 tests(커밋 기준 132), negative≥6(bad request·missing/nan/inf/bool 거부); test_check_equivalence_detects_engine_drift·test_vectorized_refuses_output_whose_nan_prefix_disagrees_with_registry = monkeypatch로 커널을 실제로 어긋나게 만들어 검사기가 잡아내는지 확인하는 진짜 실패 주입 2건 | 수치 성능 단언 없음, 게이트(CI exit code) 적색 재현 테스트 없음, D3 적대적/다중 인스턴스 증명 없음 |
| 1522 | DSL-8 runtime/series.py + interpreter.py IR 스택 실행 | 9fa72b5 | D2 | D1 | **NO** | test_interpreter.py: 15 tests, negative=17; test_interpreter_property.py: AST 트리워커 참조 구현과 150 무작위 시드 프로그램 정확 일치 + 변이 주입 시 84~113/150 시드에서 검출(변이 테스트, D3 적대적에 근접); 5000단 IR 깊이 실행(경계) | 수치 성능 단언 없음, 실패 주입은 변이-검출 property뿐 인프라 실패(DB/네트워크) 시뮬레이션 아님, 게이트 적색 재현 없음, D3 다중 인스턴스 증명 없음 |
| 1535 | DSL-12 artifact/hash.py + POST /v1/scripts/compile + 오류 위치 응답 | 8c0c10e | D2 | D1 | **NO** | test_hash.py: 11 tests negative=4; test_compile.py: 17 tests negative=4; `test_compile_elapsed_print_only`가 "DoD ≤300ms — 실측 print만(단언 금지)"라고 명시 — ADR §Decision1 "DSL 컴파일 300ms" 예산이 있는데도 코드 주석 자체가 수치 단언을 의도적으로 금지 | **수치 성능 단언을 의도적으로 회피**(ADR 명시 예산 위반 소지), 실패 주입 없음, 게이트 적색 재현 없음, D3 증명 없음 |
| 1536 | IND-9 docs/design/INDICATOR_OSS_EVAL.md 라이선스·품질 채점표 | da52f3c | — | D0 | **NO** | 순수 문서 산출물(108줄), 코드·테스트 변경 0건 | 증빙 자체가 구조적으로 불가능(코드 없음) — 리프 성격상 D-등급 체계 부적합이나 실측 기준으로는 D0 |
| 1554 | DSL-9a runtime/builtins_math.py + builtins_ta.py | f3a1f77 | D2 | D1 | **NO** | test_builtins_math.py: 10 tests negative=6; test_builtins_ta.py: 13 tests negative=10, monkeypatch 기반 registry_version 불일치 거부 2건(엔진 스파이로 배선 증명, 실패 주입엔 못 미침) | 수치 성능 단언 없음, 인프라 실패 주입 없음, 게이트 적색 재현 없음, D3 증명 없음 |
| 1555 | DSL-10 compat/cond_v2_bridge.py cond-v2→AIOS Script 변환 | 0d4b4ca | D2 | D1 | **NO** | test_cond_v2_bridge.py: 13 tests, negative=12(파라미터화 11건 전체 거부+위치 검증, offset 케이스 1건); `test_bridge_grammar_is_synced_with_frozen_evaluator`로 FROZEN 비수정 배선 증명; 지연은 `test_all_fixture_cases_are_covered_and_latency_printed`에서 print만 | 수치 성능 단언 없음(print만), 실패 주입 없음, 게이트 적색 재현 없음, D3 증명 없음 |
| 1558 | DSL-13a 스크립트 편집기 화면 1/2(ScriptEditorPage+scripts.compile) | af0cbf8 | D2 | D1 | **NO** | ScriptEditorPage.test.tsx: 7 tests, negative=2(SCRIPT_SYNTAX 마커·403 AUTH_MFA_REQUIRED 별도 처리); scripts.test.ts: 3 tests, negative=1(400 VALIDATION_INVALID_FIELD 상세 보존) | negative<3(에디터 자체는 2건, api-client 1건, 합쳐도 경계 수준), 실패 주입 없음, 성능 단언 없음, 게이트 적색 재현 없음 |
| 1606 | DSL-13b 스크립트 편집기 2/2(다중 오류 마커) | 3e7a27b | D2 | D1 | **NO** | ScriptEditor.test.tsx: 2 tests, negative=1(빈 markers 배열 처리); ScriptEditorPage.test.tsx 기존 커버리지 재사용 | negative<3, 실패 주입 없음, 성능 단언 없음, 게이트 적색 재현 없음 |
| 1728 | IND-15 spec.py PlotSpec 1급 필드 확장 | 1a7facb | D2 | D1 | **NO** | test_plot_spec.py: 13 tests, negative=8(잘못된 kind/scale/color_rule 등 거부) | 실패 주입 없음, 성능 단언 없음, 게이트 적색 재현 없음, D3 증명 없음 |
| 1729 | IND-10 TA-Lib 161종 지표 스펙 자동 생성 | 45b4ce4 | D2 | D1 | **NO** | test_generate_specs.py: 17 tests, negative=6(미지원 group·파라미터 매핑 실패 거부) | 실패 주입 없음, 성능 단언 없음, 게이트 적색 재현 없음, D3 증명 없음 |
| 1730 | IND-12 catalog/registry_tiers.py 3층 레지스트리 + GET /v1/indicators | 934b8d1 | D2 | D1 | **NO** | test_registry_tiers.py: 18 tests, `pytest.raises` 0건이나 행위 기반 negative 3건(타 테넌트 스크립트 항목 제외, 삭제된 이름의 stale 커서 복구, 빈 입력 페이지네이션) | negative가 명시적 거부(raises)가 아니라 행위 단언뿐(경계 수준), 실패 주입 없음, 성능 단언 없음(페이지네이션 지연 등 무단언), 게이트 적색 재현 없음 |
| 1738 | IND-7g 참조 벡터 3자 교차검증(TA-Lib C ↔ 증분 ↔ 벡터) | b613a05 | D2 | D1 | **NO** | test_reference_verify_all.py: 10 tests. **negative=3**(BBANDS 경계 실측 불일치 고정, drift 주입 시 SMA 검출, NaN 접두 불일치 주입 시 OBV 검출) = 동시에 **실패 주입 2건**(엔진 커널/talib 직접호출을 monkeypatch로 실제 어긋나게 만듦); `test_main_exits_nonzero_when_any_indicator_excluded`/`test_main_exits_zero_when_scope_excludes_the_known_failure` = **게이트 적색 재현 1건**(CLI exit code가 실제로 빨간불/초록불이 되는지 검증) | **3/4 충족, 수치 성능 단언만 부재**(대조 자체의 처리 시간 무단언) — 이 축에서 D2에 가장 근접한 리프 |
| 1915 | IND-14 IndicatorPicker 3층 탐색·검색·커서 페이지네이션 실배선 | d99cb4c | D2 | D1 | **NO** | IndicatorPicker.test.tsx: 10 tests, negative=2(빈 카탈로그 안내문, listIndicators 실패 시 ErrorMessage만 노출 — API 실패 시뮬레이션 1건은 얕은 실패 주입에 해당) | negative<3, 성능 단언 없음(디바운스 300ms는 로직 존재하나 수치 단언 없음), 게이트 적색 재현 없음, D3 증명 없음 |
| 2140 | DSL-9b runtime/builtins_strategy.py strategy.* 주문 의도 | 05b90ada | D2 | D1 | **NO** | test_builtins_strategy.py: 15 tests, negative=8(잘못된 side/qty·중복 호출 등 거부) | 실패 주입 없음, 성능 단언 없음, 게이트 적색 재현 없음, D3 증명 없음 |
| 2141 | IND-16 지표-온-지표(의존 그래프·순환 탐지·lookback 합성) | a6b9c7e4 | D2 | D1 | **NO** | test_indicator_on_indicator.py: 13 tests, negative=7(순환·자기참조·깊이상한·미지 노드 거부) | 실패 주입 없음, 성능 단언 없음(그래프 해석 지연 무단언), 게이트 적색 재현 없음, D3 증명 없음 |
| 2182 | DSL-11 script_facade.py — 기존 전략 엔진에 AIOS Script 경로 파사드 | b404e37e | D2 | D1 | **NO** | test_script_facade.py: 5 tests, negative=2(잘못된 스크립트 소스·브리지 거부식 각 raises); cond-v2와 첫 발화 봉 일치 property 1건 | **negative<3**(이 표 전체에서 가장 얕은 negative 커버리지), 실패 주입 없음, 성능 단언 없음, 게이트 적색 재현 없음 — FROZEN 전략 엔진 파사드로 실행 인접 리프인데도 D1에 그침 |
| 2307 | DSL-14 script/import/pine/{lexer,parser}.py Pine v5 부분 문법 | ed523592(+34305c83 분리, b720f908 줄수 보정) | D2 | D1 | **NO** | test_pine_lexer.py: 12 tests negative=2; test_pine_parser.py: 25 tests negative=14(허용목록 밖 구문 전부 PineSyntaxError 거부); 합산 negative=16 | 실패 주입 없음, 성능 단언 없음, 게이트 적색 재현 없음, D3 증명 없음. (참고: b720f908은 architecture 가드 300줄 상한을 실제로 위반해 veto된 뒤 수정한 커밋 — 개발 중 실제 게이트 적색이 있었으나 테스트로 재현되지는 않음) |
| 2373 | DSL-16a library/{imports,registry}.py import 해석·해시 고정 | e802ec59 | D2 | D1 | **NO** | test_script_library.py: 11 tests, negative=6(순환/자기참조·해시 불일치·미등록 import 거부) | 실패 주입 없음, 성능 단언 없음, 게이트 적색 재현 없음, D3 증명 없음 |
| 2326 | IND-13 참조 벡터 검증 잡: nightly 전수 + CI 30종 결정론 샘플 | 0b50cad8 | D2 | D1 | **NO** | test_reference_sampling.py: 7 tests, negative=1(제외 사유 문서화 확인) + **실패 주입 2건**(`test_injected_reference_drift_is_caught_by_ci_sample`, monkeypatch로 SMA/RSI 커널을 실제로 어긋나게 함) + **게이트 적색 재현 1건**(`test_nightly_script_exits_nonzero_and_prints_mismatch_name_on_drift` — nightly job이 실제로 nonzero exit) | negative<3(명시 거부는 1건뿐, 나머지는 결정론/캡 검증) — **3/4 요소는 있으나 negative 부족 + 수치 성능 단언 없음**으로 D2 미달. IND-7g와 함께 이 축에서 가장 깊음 |
| 2374 | DSL-15 pine/transpile.py Pine AST → AIOS Script 변환 | ca61dfd1 | D2 | D1 | **NO** | test_pine_transpile.py: 23 tests, negative=11(문법에 없는 구문·의미 다른 구문 PineTranspileError 거부); 공개 예제 30개(성공20/거부10) corpus 고정 | 실패 주입 없음, 성능 단언 없음, 게이트 적색 재현 없음, D3 증명 없음 |
| 2331 | [중복 취소] IND-13 nightly 전수 + CI 30종(2326과 중복 발행) | — | — | D0 | **NO** | 코드·테스트 변경 0건, note가 "[중복 취소]"로 명시 — 실질 리프는 2326 | 증빙 자체 없음(코드 없음). 실질 IND-13은 2326으로 이미 판정됨 |
| 2658 | [CA] IND-11 adapters/pandas_ta_bridge.py(2720과 중복 발행 취소) | — | — | D0 | **NO** | 코드·테스트 변경 0건, note가 "2720과 중복 발행 — 취소(코드 없음)"로 명시 — 실질 IND-11은 task-2720(아직 assigned, 이 감사 범위 밖) | 증빙 자체 없음(코드 없음). 실질 작업 미완료 |

## 발행된 DEEPEN task

D2 미달 29건 전부에 대해 `orchestrator.create_task`로 `DEEPEN <원 task id> <부족한 증빙>` implement task를 발행했다(우선순위 2, role은 원 task와 동일, tasks/ 직접 쓰기 없음). 2331/2658은 코드 자체가 없는 취소된 중복 발행이라 DEEPEN 스펙에 "코드가 없다면 실질 리프(2326/2720) 상태만 확인하고 이 task는 증거 없이 done 처리"라고 명시했다. 발행된 id 목록(원 task id -> DEEPEN task id):

| 원 task id | DEEPEN task id |
|---|---|
| 1234 | 2909 |
| 1235 | 2910 |
| 1337 | 2911 |
| 1354 | 2912 |
| 1369 | 2913 |
| 1502 | 2914 |
| 1503 | 2915 |
| 1505 | 2916 |
| 1522 | 2917 |
| 1535 | 2918 |
| 1536 | 2919 |
| 1554 | 2920 |
| 1555 | 2921 |
| 1558 | 2922 |
| 1606 | 2923 |
| 1728 | 2924 |
| 1729 | 2925 |
| 1730 | 2926 |
| 1738 | 2927 |
| 1915 | 2928 |
| 2140 | 2929 |
| 2141 | 2930 |
| 2182 | 2931 |
| 2307 | 2932 |
| 2326 | 2933 |
| 2331 | 2934 |
| 2373 | 2935 |
| 2374 | 2936 |
| 2658 | 2937 |
