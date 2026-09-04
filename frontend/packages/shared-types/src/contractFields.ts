// task-1239 — 프론트 파서(positionView/candleSeries/ledgerView/holdPayoutView/
// instrumentView, 총 5개 파일)가 손으로 미러링한 필드 집합의 단일출처 선언.
//
// 각 항목은 "이 pydantic 클래스의 필드와 저 TS parser의 isXXXBody가 정확히
// 같은 이름 집합을 요구한다"는 주장이다. contractDrift.test.ts가 file/className을
// node:fs로 읽어 실제 pydantic 필드명을 추출하고, 여기 선언된 fields와 1:1
// 대조한다 — 누락·오타·추가 전부 실패다.
//
// schema_version은 여기 선언하지 않는다: 5개 파서 모두 parseSchemaTagged가
// "v1" 리터럴 여부를 필드 검증과 별도로 먼저 검사하고(positionView.ts 등
// isXXXBody 함수들 참고), TS의 View 인터페이스 자체에도 schema_version이
// 없다 — 뷰 필드가 아니라 계약 버전 태그이기 때문이다. PostingLine은
// v1.py에도 schema_version이 아예 없다(중첩 값 객체, 그 자체로 버전 태그가
// 없음). contractDrift.test.ts는 추출한 실제 필드에서 schema_version을
// 양쪽 다 제외하고 비교한다 — 즉 여기 목록에 schema_version을 넣지 않는다.

export interface ContractFieldSpec {
  /** 이 필드 집합의 SSOT인 pydantic 모듈의 저장소 상대 경로. */
  readonly file: string;
  /** file 안의 pydantic BaseModel 서브클래스 이름. */
  readonly className: string;
  /** 대응하는 프론트 parser 함수(들) — 추적용, 대조에는 쓰이지 않는다. */
  readonly parser: string;
  /** 프론트 isXXXBody가 요구하는 필드명 집합(schema_version 제외). */
  readonly fields: readonly string[];
}

const POSITIONS_V1 = "src/foundation/positions/contracts/v1.py";
const MARKET_DATA_V1 = "src/foundation/market_data/contracts/v1.py";
const LEDGER_V1 = "src/foundation/ledger/contracts/v1.py";
const HEALTH_ROUTER = "src/api/routers/health.py";
const AUTH_TOKENS = "src/services/auth/tokens.py";
const SECRET_REF = "src/core/security/secret_ref.py";

export const CONTRACT_FIELD_SPECS: readonly ContractFieldSpec[] = [
  // ---- positionView.ts (§3.2 B) ----
  {
    file: POSITIONS_V1,
    className: "PositionSnapshotView",
    parser: "parsePositionSnapshot",
    fields: [
      "position_key",
      "tenant_id",
      "account_id",
      "instrument_id",
      "quantity",
      "avg_cost",
      "cost_method",
      "lots",
      "realized_pnl_base",
      "unrealized_pnl_base",
      "fees_base",
      "funding_base",
      "mark_price",
      "mark_at",
      "base_currency",
      "last_journal_seq",
      "updated_at",
    ],
  },
  {
    file: POSITIONS_V1,
    className: "PnLBreakdown",
    parser: "parsePnLBreakdown",
    fields: ["realized", "unrealized", "fees", "funding", "total", "base_currency", "fx_rates_used"],
  },
  {
    file: POSITIONS_V1,
    className: "NAVSnapshot",
    parser: "parseNavSnapshot",
    fields: [
      "account_id",
      "nav_date",
      "base_currency",
      "opening_nav",
      "cash",
      "positions_mv",
      "realized",
      "unrealized_delta",
      "funding",
      "fees",
      "flows",
      "closing_nav",
      "fx_rates",
      "source_hash",
    ],
  },

  // ---- candleSeries.ts (§3.1 A) ----
  {
    file: MARKET_DATA_V1,
    className: "CandleSeries",
    parser: "parseCandleSeries",
    fields: ["key", "candles", "gaps", "adjustment", "as_of", "series_hash"],
  },
  {
    file: MARKET_DATA_V1,
    className: "QualityVerdict",
    parser: "parseQualityVerdict",
    fields: ["verdict", "accepted", "quarantined", "rejected", "issues"],
  },

  // ---- ledgerView.ts (§3.3 C) ----
  {
    file: LEDGER_V1,
    className: "JournalEntryView",
    parser: "parseJournalEntryView",
    fields: [
      "entry_id",
      "sequence_no",
      "event_type",
      "event_ref",
      "idempotency_key",
      "lines",
      "lines_digest",
      "prev_hash",
      "entry_hash",
      "audit_event_id",
      "posted_at",
      "replayed",
    ],
  },
  {
    file: LEDGER_V1,
    className: "BalanceView",
    parser: "parseBalanceView",
    fields: [
      "account_code",
      "balance",
      "held",
      "available",
      "pending_payout",
      "currency",
      "last_entry_seq",
      "as_of",
    ],
  },
  {
    file: LEDGER_V1,
    className: "PostingLine",
    parser: "parsePostingLine / isPostingLine",
    fields: ["line_no", "account_code", "side", "amount", "currency"],
  },

  // ---- holdPayoutView.ts (§3.3 C) ----
  {
    file: LEDGER_V1,
    className: "HoldView",
    parser: "parseHoldView",
    fields: [
      "hold_id",
      "account_code",
      "amount",
      "purpose",
      "reference",
      "state",
      "expires_at",
      "entry_id",
    ],
  },
  {
    file: LEDGER_V1,
    className: "PayoutBatchView",
    parser: "parsePayoutBatchView",
    fields: [
      "batch_id",
      "seller_user_id",
      "period_start",
      "period_end",
      "amount",
      "state",
      "capture_entry_ids",
      "release_entry_id",
      "paid_entry_id",
    ],
  },

  // ---- instrumentView.ts (§3.1 A InstrumentRef) ----
  // TS의 InstrumentView는 py InstrumentRef 1:1 대응(파일 자체 주석 참고).
  // SymbolAlias는 v1.py에 SSOT 클래스가 없는 DB 행 형태라 여기 선언하지 않는다.
  {
    file: MARKET_DATA_V1,
    className: "InstrumentRef",
    parser: "parseInstrumentView",
    fields: [
      "instrument_id",
      "venue",
      "canonical_symbol",
      "venue_symbol",
      "asset_class",
      "base",
      "quote",
      "tick_size",
      "lot_size",
      "status",
      "listed_at",
      "delisted_at",
    ],
  },

  // ---- readiness.ts (§3.2 헬스·메트릭, task-1332 배치2) ----
  {
    file: HEALTH_ROUTER,
    className: "ReadinessReport",
    parser: "parseReadiness / isReadinessReport",
    fields: ["status", "checks", "as_of"],
  },
  {
    file: HEALTH_ROUTER,
    className: "CheckResult",
    parser: "isCheckResult",
    fields: ["ok", "detail", "observed", "threshold"],
  },

  // ---- tokenPair.ts (§3.4 인증 토큰, task-1332 배치2) ----
  {
    file: AUTH_TOKENS,
    className: "TokenPairResponse",
    parser: "parseTokenPair",
    fields: ["access_token", "refresh_token", "token_type", "expires_in", "session_id"],
  },

  // ---- secretRef.ts (§3.6 비밀 계약, task-1332 배치2) ----
  {
    file: SECRET_REF,
    className: "SecretRef",
    parser: "parseSecretRef / formatSecretRef",
    fields: ["scope", "kind", "id", "kid"],
  },
];

// task-1332 배치2 note — 이 리프에서 함께 다루지 않은 것:
// §3.4 SessionView(frontend/packages/shared-types/src/session.ts)와 §3.5
// MembershipView(spec 379-390행)는 백엔드에 대응하는 pydantic SSOT 클래스가
// 아직 없다. TenantContext에는 membership_id 필드만 추가돼 있고(PLT-28),
// MembershipView/TenantKind/MembershipRole/MembershipState는
// src/foundation/trust/contracts/v1.py에 전혀 없다(grep 확인, 2026-09-04).
// session_id/created_at/last_seen_at/user_agent/ip/revoked_at을 반환하는
// 세션 목록 API도 아직 없다(tokenPair.ts 주석 "서버 PLT-23/24는 아직 미구현"
// 참고). 존재하지 않는 백엔드 클래스를 겨냥한 스펙은
// contractDrift.test.ts의 `expect(actual).not.toBeNull()`에 항상 걸려
// 드리프트가 아니라 "클래스가 없다"는 이유로 상시 FAIL한다 — 이는 드리프트
// 가드가 아니라 백엔드 구현을 기다리는 표식일 뿐이므로 여기 등재하지
// 않는다. 백엔드가 두 계약을 구현하면 후속 리프에서 채운다.
