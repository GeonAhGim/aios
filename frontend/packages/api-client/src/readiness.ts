// spec §3.2 헬스·메트릭(ReadinessReport/CheckResult). `/readyz`는 다른 엔드포인트와
// 달리 ApiResponse 봉투(§3.3)를 안 씌울 수 있으므로, 이 모듈은 봉투 유무 양쪽을
// 모두 받아들인다: raw 자체가 ReadinessReport이거나, raw.data가 ReadinessReport다.
//
// 범위 제한(이 leaf의 decision): http.ts·envelope.ts·shared-types/index.ts는 건드리지
// 않는다(task-465와 충돌 방지). 폴링·재시도·전역 상태는 여기서 다루지 않는다 —
// 순수 파싱 + 요약 함수만 제공하고, 배선은 useReadiness.ts(그리고 그 상위)가 한다.

/** contracts/health.py CheckResult와 1:1 대응. */
export interface CheckResult {
  ok: boolean;
  detail: string | null;
  observed: number | null;
  threshold: number | null;
}

/** contracts/health.py ReadinessReport와 1:1 대응. checks 키는 db_pool, migration_head,
 * event_bus, loop:<name>... 등 서버가 추가할 수 있으므로 화이트리스트를 두지 않는다. */
export interface ReadinessReport {
  status: "ready" | "not_ready";
  checks: Record<string, CheckResult>;
  as_of: string;
}

export type ParsedReadiness =
  | { kind: "ok"; report: ReadinessReport }
  | { kind: "unknown" };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isNullableString(value: unknown): value is string | null {
  return value === null || value === undefined || typeof value === "string";
}

function isNullableNumber(value: unknown): value is number | null {
  return value === null || value === undefined || typeof value === "number";
}

function isCheckResult(value: unknown): value is CheckResult {
  if (!isRecord(value)) return false;
  return (
    typeof value.ok === "boolean" &&
    isNullableString(value.detail) &&
    isNullableNumber(value.observed) &&
    isNullableNumber(value.threshold)
  );
}

function isReadinessReport(value: unknown): value is ReadinessReport {
  if (!isRecord(value)) return false;
  if (value.status !== "ready" && value.status !== "not_ready") return false;
  if (typeof value.as_of !== "string") return false;
  if (!isRecord(value.checks)) return false;
  return Object.values(value.checks).every(isCheckResult);
}

/**
 * §3.2 ReadinessReport를 파싱한다. 봉투가 있으면(raw.data) 그 안을, 없으면 raw
 * 자체를 후보로 검사한다. 스키마가 안 맞거나 응답이 없으면 "unknown" — not_ready로
 * 단정하지 않는다(응답 부재와 실제 저하 상태는 다른 사실이다).
 */
export function parseReadiness(raw: unknown): ParsedReadiness {
  if (!isRecord(raw)) return { kind: "unknown" };

  const candidate = "data" in raw && isRecord(raw.data) ? raw.data : raw;
  if (!isReadinessReport(candidate)) return { kind: "unknown" };

  return { kind: "ok", report: candidate };
}

export interface FailedCheck {
  name: string;
  detail: string | null;
}

export interface ReadinessSummary {
  status: "ready" | "not_ready" | "unknown";
  failedChecks: FailedCheck[];
}

/** 실패한(ok=false) check만 이름·detail로 추려 반환한다. checks가 빈 객체여도
 * status는 서버 판정을 그대로 따를 뿐, "실패 없음"을 "ready"로 역추정하지 않는다. */
export function summarizeReadiness(parsed: ParsedReadiness): ReadinessSummary {
  if (parsed.kind === "unknown") {
    return { status: "unknown", failedChecks: [] };
  }

  const failedChecks = Object.entries(parsed.report.checks)
    .filter(([, check]) => !check.ok)
    .map(([name, check]) => ({ name, detail: check.detail }));

  return { status: parsed.report.status, failedChecks };
}
