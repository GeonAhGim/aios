// src/foundation/validation/contracts/v1.py ValidationResultView 1:1 대응(task-2412
// FE-OPS-8, POST /v1/foundation/validation-runs/{strategy_id}/{strategy_version}).
// http.ts의 requestEnvelope는 data를 camelCase로 바꾸므로 clients/validation.ts가
// keysToSnake로 되돌린 뒤 이 파서에 넘긴다(positions.ts와 동일 관용, task-1524
// decision — 새 파싱 방식을 만들지 않는다).
//
// schema_version이 "v1"이 아니면(누락 포함) 구조 검증보다 먼저 null을 반환한다 —
// 무음으로 통과시키지 않는다(DoD c).

export type ValidationRunState = "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";

export type ValidationOutcome = "PASS" | "FAIL" | "PASS_WITH_OBLIGATIONS";

export interface ValidationResultView {
  run_id: string;
  strategy_id: string;
  strategy_version: string;
  check_type: string;
  state: ValidationRunState;
  outcome: ValidationOutcome | null;
  metrics: Record<string, unknown> | null;
  warnings: string[];
  hard_fail_reasons: string[];
  obligations: string[];
  result_hash: string | null;
  created_at: string;
}

// StartValidationRequest(src/api/schemas/foundation/validation.py) 요청 body — camelCase로
// 선언한다. http.ts의 postEnvelope가 keysToSnake로 자동 변환해 서버가 기대하는
// snake_case로 나간다(strategy.ts/mandates.ts와 동일 관용).
export interface StartValidationRequest {
  exchange: string;
  symbol: string;
  timeframe?: string;
  limit?: number;
  costModelFeeBps: number;
  costModelSlippageBps: number;
  warmupBars?: number;
  periodsPerYear?: number;
  initialEquity?: number;
}

const RUN_STATES: ReadonlySet<string> = new Set(["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]);
const OUTCOMES: ReadonlySet<string> = new Set(["PASS", "FAIL", "PASS_WITH_OBLIGATIONS"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isNullableString(value: unknown): value is string | null {
  return value === null || value === undefined || typeof value === "string";
}

function isValidationResultBody(value: Record<string, unknown>): boolean {
  return (
    typeof value.run_id === "string" &&
    typeof value.strategy_id === "string" &&
    typeof value.strategy_version === "string" &&
    typeof value.check_type === "string" &&
    typeof value.state === "string" &&
    RUN_STATES.has(value.state) &&
    (value.outcome === null || (typeof value.outcome === "string" && OUTCOMES.has(value.outcome))) &&
    (value.metrics === null || isRecord(value.metrics)) &&
    isStringArray(value.warnings) &&
    isStringArray(value.hard_fail_reasons) &&
    isStringArray(value.obligations) &&
    isNullableString(value.result_hash) &&
    typeof value.created_at === "string"
  );
}

/** ApiResponse 봉투({data, meta})가 있으면 그 안을, 없으면 raw 자체를 후보로 본다
 * (ledgerView.ts와 동일 관용). 구조가 안 맞거나 schema_version이 "v1"이 아니면
 * null — 판별 결과 대신 예외를 던지지 않는다. */
export function parseValidationResultView(raw: unknown): ValidationResultView | null {
  const candidate = isRecord(raw) && "data" in raw ? raw.data : raw;
  if (!isRecord(candidate)) return null;
  if (candidate.schema_version !== "v1") return null;
  if (!isValidationResultBody(candidate)) return null;
  return candidate as unknown as ValidationResultView;
}
