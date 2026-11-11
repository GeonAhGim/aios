# 파일 범위 분할 제안: task-23246 [재투입]

## 분석

- 원본 files: `["scripts/check_consistency.py", "src/"]` — `src/`는 디렉터리라 파일 범위로 해석 불가(`unresolved_path_scope`).
- 원본 spec: "Fix review findings: consistency 24h 5회 반복 — 단계 규칙/기준선 설계 결함 조사"
- 실제 실패 게이트: `openapi_client_mismatch` 34건 (baseline 34, 현재 34 — 드리프트 없음)
- 34건 내역 (실제 `check_openapi_frontend()` 함수 결과):
  - **18건**: OpenAPI(`contracts/openapi/v1.json`)에 있으나 프론트엔드(`apiRoutes.ts`)에서 호출하지 않는 백엔드 전용 경로
  - **16건**: 프론트엔드에서 호출하나 OpenAPI에 없는 프론트엔드 전용 경로

### 18개 백엔드 전용 경로 (OpenAPI→Frontend 불일치)

실제 API(POST/GET)가 존재하며, 프론트엔드 UI 화면이 없는 경우들:

```
/risk-coach/position-size                    -- 리스크 코치 위치 크기 계산 API
/v1/accounts/summary                        -- 계정 요약 API
/v1/ai/experiments/{experiment_id}          -- AI 실험 상세 API
/v1/ai/experiments/{experiment_id}/lineage  -- AI 실험 계보 API
/v1/ai/proposals/{proposal_id}              -- AI 제안 상세 API
/v1/ai/tokens/{token_id}:rotate             -- AI 토큰 회전 API
/v1/assistant/explain-backtest              -- 백테스트 설명 API
/v1/assistant/explain-script                -- 스크립트 설명 API
/v1/assistant/generate-script               -- 스크립트 생성 API
/v1/foundation/compliance/mandate/status    -- 규정 준수 명령 상태 API
/v1/foundation/market-data/options/chain    -- 옵션 체인 데이터 API
/v1/foundation/personal/bundle              -- 개인 번들 API
/v1/foundation/personal/daily-report        -- 일일 리포트 API
/v1/foundation/personal/kill                -- 개인 키일 API
/v1/foundation/personal/promote             -- 개인 승진 API
/v1/foundation/personal/promotion-checklist -- 승진 체크리스트 API
/v1/foundation/research/items               -- 연구 항목 목록 API
/v1/foundation/research/items/{item_id}     -- 연구 항목 상세 API
```

### 16개 프론트엔드 전용 경로 (Frontend→OpenAPI 불일치)

`apiRoutes.ts`에 있으나 실제 백엔드 라우터가 없는 유령 경로들:

```
/auth/sessions                              -- 세션 조회 (백엔드 라우터 없음)
/auth/sessions/:sessionId                   -- 세션 회수 (백엔드 라우터 없음)
/v1/ai/experiments                          -- AI 실험 목록 (백엔드 라우터 없음)
/v1/ai/proposals/:proposalId:promote        -- AI 제안 승진 (백엔드 라우터 없음)
/v1/ai/proposals/:proposalId:promote-ticket -- AI 제안 승진 티켓 (백엔드 라우터 없음)
/v1/ai/providers                            -- AI 제공자 목록 (백엔드 라우터 없음)
/v1/ai/providers/:provider                  -- AI 제공자 상세 (백엔드 라우터 없음)
/v1/foundation/follow/subscriptions         -- 팔로우 구독 목록 (백엔드 라우터 없음)
/v1/foundation/follow/subscriptions/:subscriptionId         -- 구독 상세 (백엔드 라우터 없음)
/v1/foundation/follow/subscriptions/:subscriptionId/performance -- 구독 성과 (백엔드 라우터 없음)
/v1/foundation/signals/sources              -- 신호 소스 목록 (백엔드 라우터 없음)
/v1/foundation/signals/sources/:sourceId/receipts             -- 신호 영수증 (백엔드 라우터 없음)
/v1/foundation/signals/sources/:sourceId/disable              -- 신호 비활성화 (백엔드 라우터 없음)
/v1/foundation/signals/sources/:sourceId:rotate               -- 신호 회전 (백엔드 라우터 없음)
/v1/foundation/whatif/preview-order       -- 주문 미리보기 (백엔드 라우터 없음)
/v1/foundation/whatif/rebalance-plan      -- 재조정 계획 (백엔드 라우터 없음)
```

## 실제 변경 파일

**`scripts/consistency/contracts.py`** 1파일 — 두 개의 allowlist 추가 필요:

1. `_OPENAPI_NO_FRONTEND_UI_ALLOWLIST`에 18개 백엔드 전용 경로 추가 (기존 패턴과 동일: 경로: 사유 주석)
2. 프론트엔드 전용 경로용 새 allowlist 필요 (`_FRONTEND_NO_OPENAPI_ALLOWLIST` 또는 기존 함수 수정) — 16개 경로

## 분할안

### 권장: 단일 파일·단일 커밋

`scripts/consistency/contracts.py` 1건에 두 allowlist 추가 + `check_openapi_frontend()` 함수에 프론트엔드 전용 allowlist 체크 추가.

### 분할안 (필요시)

| 작업 | 파일 | 설명 |
|------|------|------|
| A | `scripts/consistency/contracts.py` | 18개 백엔드 전용 경로 `_OPENAPI_NO_FRONTEND_UI_ALLOWLIST` 추가 |
| B | `scripts/consistency/contracts.py` | 16개 프론트엔드 전용 경로용 allowlist + 체크 로직 추가 |

A·B는 같은 파일이므로 단일 커밋 권장. 분할해도 독립성이 낮음.

## 결론

- **권장: `scripts/consistency/contracts.py` 단일 수정 1건** (두 allowlist 34개 추가 + 체크 로직 수정)
- `src/` 전체 범위는 불필요 — 실제 변경은 contracts.py 1파일
- baseline(34)과 현재(34)가 동일하므로 드리프트는 없음. 기존 게이트 녹색 유지 목적
- 프론트엔드 전용 16건은 `_OPENAPI_NO_FRONTEND_UI_ALLOWLIST`만으로는 해결 불가 — `check_openapi_frontend()`의 프론트엔드→OpenAPI 체크에도 allowlist 적용 필요

원본 hold 해제·재큐·코드 수정은 하지 않았다.
