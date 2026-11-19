# 프런트엔드 명세 (docs/frontend)

글로벌 규모 프런트엔드 프로그램의 정본 위치. 결정 기록: `docs/design/ADR-2026-11-18-B-global-grade-frontend-program.md`.

| 파일 | 내용 | 상태 |
|---|---|---|
| `SCREEN_SPEC_TEMPLATE.md` | 화면 기능 명세 템플릿(FE-1) | 확정 |
| `XCUT_RULES.md` | 횡단 규칙집(FE-2) | Phase 0 작성 중 |
| `UNSPECIFIED_ACTIONS.md` | 미규정 액션 레지스트리(D4) | 시작 |
| `PRO_WORKSPACE_DESIGN.md` | Pro Workspace 설계(FE-9): 패널·맥락 그룹·다중 거래소 정규화·밀도 단계 | v0.2(비교 분석 반영, 기본안 승인) |
| `../uat/UAT_GUIDE.md` | 체험(UAT) 사용 안내 -- 접속·첫 로그인 준비물·되는 것/막힌 것 | 작성 |
| `PERSONAS_AND_JOURNEYS.md` | 사용자 기준 페르소나 P1~P11·여정 지도·역할/맥락 모델·플러그인 모델 | v0.1 |
| `COMPETITIVE_ANALYSIS.md` | 경쟁 제품 비교와 설계 결정 근거 | 작성(출처 6건) |
| `WIRING_FINDINGS_2026-11-19.md` | 프런트-백엔드 배선 진위 점검 결과(실스택) | 작성 |
| `WIRING_REPORT.md` | 배선 전수 점검 생성 보고서(`scripts/uat/wiring_probe.py`) | 생성물 |
| `screens/` | 화면별 명세 — J1~J3 먼저(Phase 0), 나머지는 Phase 1 | 작성 중 |
| `spec_manifest.json` | Phase 0 화면 명세 대상 목록(사실 추출기·발행기의 단일 입력) | 생성 |
| `facts/` | **자동 추출 사실 시트**(코드에서 기계가 뽑음, 줄 번호 인용, 편집 금지) | 생성물 |

## 명세 작성 절차 (신뢰 규칙)

1. 사실은 `facts/<Page>.facts.md`에서 가져온다(코드에서 기계가 추출, `python -m scripts.frontend_spec.gen_facts`로 재생성, 최신성은 테스트가 검사).
2. 명세 표의 모든 코드 주장은 `파일:줄` 인용과 함께 쓰고, `python -m scripts.frontend_spec.verify_spec --check`가 통과해야 한다(없는 필드·틀린 줄 번호는 기계가 적발).
3. 사람/모델의 **판단**(상태 매트릭스, 갭, 수용 기준)은 사실 시트와 구분해 "해석"으로 표시한다.

## 규칙

1. 화면·횡단 동작을 바꾸는 리프는 spec에 해당 명세 절을 링크한다.
2. 명세와 구현이 어긋나면 명세가 기준이다. 어긋남은 각 명세의 갭 표(§12)에 적고 리프로 해소한다.
3. 사실은 코드·API 스키마·실스택 관찰로 확인한 것만 적는다. 추측은 "미확인"으로 표시한다.
4. 명세에 없던 동작을 발견하면 `UNSPECIFIED_ACTIONS.md`에 기록하고, 횡단 규칙/화면 명세/백엔드 계약 중 한 곳으로 귀속해 **명세를 먼저 갱신**한 뒤 구현한다.

## 문서를 읽는 게이트를 만들 때의 체크리스트 (2026-11-19 사고에서 나온 규칙)

명세 문서는 코드가 아니지만 **테스트의 입력**이다(`verify_spec` 기준선 테스트가 `docs/frontend/**`를 읽는다). 문서를 쓰는 워커 한 명이 main의
전체 pytest를 적색으로 만들 수 있고, 실제로 2026-11-19에 큐가 20개 배치를 6시간 가까이 되돌렸다. 문서·생성물을 읽는 게이트나 테스트를 새로 만들 때는
아래 네 가지를 **같은 리프에서 함께** 등록한다. 하나라도 빠지면 그 게이트는 main에서 처음 터진다.

1. **영향 범위 매핑**: 그 문서 경로를 바꾸면 그 테스트가 영향 범위에 들어가게 한다(`pm/ci_impact.py`의 `DOC_INPUT_PREFIXES`). 문서는 import가 없어 역의존에 안 잡힌다.
2. **워커 사전 게이트**: 리프가 push하기 전에 같은 검사를 돌린다(`pm/worker_runner.py`의 `leaf_dod_commands`). 머지 단계에서 알면 한 번의 실행이 통째로 낭비된다.
3. **큐 표적 probe**: 사전점검이 이 테스트를 잡으면 큐가 그 테스트만으로 범인 리프를 찾는다(`merge_queue.bisect_culprit`가 자동). 별도 등록은 필요 없지만 테스트 nodeid가 안정적이어야 한다.
4. **기준선 방향**: 기준선은 줄어드는 것만 허용한다(래칫). 늘리는 변경은 `premerge_weakening`이 의심으로 올린다.

## 실스택 관찰 방법

UAT 스택(`python scripts/uat/up.py up`, 웹 :18080 / API :18000)과 `cd frontend && npm run test:uat`로 실제 동작을 확인한다.
