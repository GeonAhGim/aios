# 프런트엔드 명세 (docs/frontend)

글로벌 규모 프런트엔드 프로그램의 정본 위치. 결정 기록: `docs/design/ADR-2026-11-18-B-global-grade-frontend-program.md`.

| 파일 | 내용 | 상태 |
|---|---|---|
| `SCREEN_SPEC_TEMPLATE.md` | 화면 기능 명세 템플릿(FE-1) | 확정 |
| `XCUT_RULES.md` | 횡단 규칙집(FE-2) | Phase 0 작성 중 |
| `UNSPECIFIED_ACTIONS.md` | 미규정 액션 레지스트리(D4) | 시작 |
| `PRO_WORKSPACE_DESIGN.md` | Pro Workspace 설계(FE-9): 패널·맥락 그룹·다중 거래소 정규화·밀도 단계 | v0.2(비교 분석 반영, 기본안 승인) |
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

## 실스택 관찰 방법

UAT 스택(`python scripts/uat/up.py up`, 웹 :18080 / API :18000)과 `cd frontend && npm run test:uat`로 실제 동작을 확인한다.
