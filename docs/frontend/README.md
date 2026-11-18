# 프런트엔드 명세 (docs/frontend)

글로벌 규모 프런트엔드 프로그램의 정본 위치. 결정 기록: `docs/design/ADR-2026-11-18-B-global-grade-frontend-program.md`.

| 파일 | 내용 | 상태 |
|---|---|---|
| `SCREEN_SPEC_TEMPLATE.md` | 화면 기능 명세 템플릿(FE-1) | 확정 |
| `XCUT_RULES.md` | 횡단 규칙집(FE-2) | Phase 0 작성 중 |
| `UNSPECIFIED_ACTIONS.md` | 미규정 액션 레지스트리(D4) | 시작 |
| `PRO_WORKSPACE_DESIGN.md` | Pro Workspace 설계(FE-9): 패널·맥락 그룹·다중 거래소 정규화·밀도 단계 | v0.2(비교 분석 반영, 기본안 승인) |
| `COMPETITIVE_ANALYSIS.md` | 경쟁 제품 비교와 설계 결정 근거 | 작성(출처 6건) |
| `screens/` | 화면별 명세 — J1~J3 먼저(Phase 0), 나머지는 Phase 1 | 작성 중 |

## 규칙

1. 화면·횡단 동작을 바꾸는 리프는 spec에 해당 명세 절을 링크한다.
2. 명세와 구현이 어긋나면 명세가 기준이다. 어긋남은 각 명세의 갭 표(§12)에 적고 리프로 해소한다.
3. 사실은 코드·API 스키마·실스택 관찰로 확인한 것만 적는다. 추측은 "미확인"으로 표시한다.
4. 명세에 없던 동작을 발견하면 `UNSPECIFIED_ACTIONS.md`에 기록하고, 횡단 규칙/화면 명세/백엔드 계약 중 한 곳으로 귀속해 **명세를 먼저 갱신**한 뒤 구현한다.

## 실스택 관찰 방법

UAT 스택(`python scripts/uat/up.py up`, 웹 :18080 / API :18000)과 `cd frontend && npm run test:uat`로 실제 동작을 확인한다.
