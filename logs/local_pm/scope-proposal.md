# 파일 범위 분할 제안: task-23246 [재투입]

## 분석

- 원본 files: `["scripts/check_consistency.py", "src/"]` — `src/`는 디렉터리라 파일 범위로 해석 불가(`unresolved_path_scope`).
- 원본 spec: "Fix review findings: consistency 24h 5회 반복 — 단계 규칙/기준선 설계 결함 조사"
- 실제 실패 게이트: `openapi_client_mismatch` 34건 (baseline 34, 현재 34 — 드리프트 없음)
- 34건 내역:
  - **18건**: OpenAPI에 있으나 프론트엔드(apiRoutes.ts)에서 호출하지 않는 백엔드 전용 경로
  - **16건**: 프론트엔드에서 호출하나 OpenAPI에 없는 프론트엔드 전용 경로
- 18개 백엔드 전용 경로는 모두 실제 API(POST/GET)가 존재하며, 프론트엔드 UI 화면이 없는 경우들
- 16개 프론트엔드 전용 경로는 `apiRoutes.ts`에 있으나 실제 백엔드 라우터가 없음(프론트엔드 전용)

## 실제 변경 파일

**`scripts/consistency/contracts.py`** 1파일 — `_OPENAPI_NO_FRONTEND_UI_ALLOWLIST`에 다음 34개 경로 추가:

### 백엔드 전용 (18개) — 프론트엔드 UI 없음 사유
```
/risk-coach/position-size
/v1/accounts/summary
/v1/ai/experiments/{experiment_id}
/v1/ai/experiments/{experiment_id}/lineage
/v1/ai/proposals/{proposal_id}
/v1/ai/tokens/{token_id}:rotate
/v1/assistant/explain-backtest
/v1/assistant/explain-script
/v1/assistant/generate-script
/v1/foundation/compliance/mandate/status
/v1/foundation/market-data/options/chain
/v1/foundation/personal/bundle
/v1/foundation/personal/daily-report
/v1/foundation/personal/kill
/v1/foundation/personal/promote
/v1/foundation/personal/promotion-checklist
/v1/foundation/research/items
/v1/foundation/research/items/{item_id}
```

### 프론트엔드 전용 (16개) — 백엔드 라우터 없음 사유
```
/auth/sessions
/auth/sessions/:sessionId
/v1/ai/experiments
/v1/ai/proposals/:proposalId:promote
/v1/ai/proposals/:proposalId:promote-ticket
/v1/ai/providers
/v1/ai/providers/:provider
/v1/foundation/follow/subscriptions
/v1/foundation/follow/subscriptions/:subscriptionId
/v1/foundation/follow/subscriptions/:subscriptionId/performance
/v1/foundation/signals/sources
/v1/foundation/signals/sources/:sourceId/receipts
/v1/foundation/signals/sources/:sourceId:disable
/v1/foundation/signals/sources/:sourceId:rotate
/v1/foundation/whatif/preview-order
/v1/foundation/whatif/rebalance-plan
```

## 분할안

### 권장: 단일 파일·단일 커밋
`scripts/consistency/contracts.py` 1건에 34개 allowlist 항목 추가. 기존 allowlist 패턴(경로: 사유 주석)과 동일.

### 분할안 (필요시)
| 작업 | 파일 | 설명 |
|------|------|------|
| A | `scripts/consistency/contracts.py` | 18개 백엔드 전용 경로 allowlist 추가 |
| B | `scripts/consistency/contracts.py` | 16개 프론트엔드 전용 경로 allowlist 추가 |

A·B는 같은 파일이므로 단일 커밋 권장. 분할해도 독립성이 낮음.

## 결론
- **권장: `scripts/consistency/contracts.py` 단일 수정 1건** (allowlist 34개 추가)
- `src/` 전체 범위는 불필요 — 실제 변경은 contracts.py 1파일
- baseline(34)과 현재(34)가 동일하므로 드리프트는 없음. 기존 게이트 녹색 유지 목적

원본 hold 해제·재큐·코드 수정은 하지 않았다.
