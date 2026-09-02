import { describe, expect, it } from "vitest";
import { parseReadiness, summarizeReadiness } from "./readiness";

const READY_REPORT = {
  status: "ready",
  checks: {
    db_pool: { ok: true, detail: null, observed: 3, threshold: 10 },
    migration_head: { ok: true, detail: null, observed: null, threshold: null },
  },
  as_of: "2026-09-03T00:00:00Z",
};

const NOT_READY_REPORT = {
  status: "not_ready",
  checks: {
    db_pool: { ok: true, detail: null, observed: 1, threshold: 10 },
    event_bus: { ok: false, detail: "no consumers", observed: 0, threshold: 1 },
    "loop:trading": { ok: false, detail: null, observed: 900, threshold: 300 },
  },
  as_of: "2026-09-03T00:00:00Z",
};

describe("parseReadiness", () => {
  it("봉투 없이 ReadinessReport 자체를 파싱한다", () => {
    const parsed = parseReadiness(READY_REPORT);
    expect(parsed).toEqual({ kind: "ok", report: READY_REPORT });
  });

  it("ApiResponse 봉투({data, meta})로 감싼 응답도 파싱한다", () => {
    const parsed = parseReadiness({
      data: NOT_READY_REPORT,
      meta: { trace_id: "t1", as_of: "2026-09-03T00:00:00Z", page: null },
    });
    expect(parsed).toEqual({ kind: "ok", report: NOT_READY_REPORT });
  });

  it("미지 check 키(loop:trading)를 그대로 보존한다(전방호환)", () => {
    const parsed = parseReadiness(NOT_READY_REPORT);
    expect(parsed.kind).toBe("ok");
    if (parsed.kind === "ok") {
      expect(parsed.report.checks["loop:trading"]).toEqual({
        ok: false,
        detail: null,
        observed: 900,
        threshold: 300,
      });
    }
  });

  it("negative: 응답이 없으면(null/undefined) unknown이다", () => {
    expect(parseReadiness(null)).toEqual({ kind: "unknown" });
    expect(parseReadiness(undefined)).toEqual({ kind: "unknown" });
  });

  it("negative: status 값이 스키마와 다르면(예: 'healthy') unknown이다", () => {
    const malformed = { ...READY_REPORT, status: "healthy" };
    expect(parseReadiness(malformed)).toEqual({ kind: "unknown" });
  });

  it("negative: status 필드가 없고 checks만 빈 객체여도 ready로 단정하지 않고 unknown이다", () => {
    const malformed = { checks: {}, as_of: "2026-09-03T00:00:00Z" };
    expect(parseReadiness(malformed)).toEqual({ kind: "unknown" });
  });

  it("negative: checks의 한 항목이 CheckResult 스키마를 어기면(ok가 문자열) unknown이다", () => {
    const malformed = {
      status: "ready",
      checks: { db_pool: { ok: "true", detail: null, observed: null, threshold: null } },
      as_of: "2026-09-03T00:00:00Z",
    };
    expect(parseReadiness(malformed)).toEqual({ kind: "unknown" });
  });
});

describe("summarizeReadiness", () => {
  it("ready 응답은 failedChecks가 빈 배열이다", () => {
    const summary = summarizeReadiness(parseReadiness(READY_REPORT));
    expect(summary).toEqual({ status: "ready", failedChecks: [] });
  });

  it("not_ready 응답은 ok=false인 check만 이름·detail로 추린다", () => {
    const summary = summarizeReadiness(parseReadiness(NOT_READY_REPORT));
    expect(summary.status).toBe("not_ready");
    expect(summary.failedChecks).toEqual([
      { name: "event_bus", detail: "no consumers" },
      { name: "loop:trading", detail: null },
    ]);
  });

  it("negative: checks가 빈 객체이면 status가 ready여도 실패 목록만 비고 ready를 임의로 단정하지 않는다(서버 값 그대로 전달)", () => {
    const emptyChecks = { status: "not_ready", checks: {}, as_of: "2026-09-03T00:00:00Z" };
    const summary = summarizeReadiness(parseReadiness(emptyChecks));
    expect(summary).toEqual({ status: "not_ready", failedChecks: [] });
  });

  it("unknown 파싱 결과는 status가 unknown이고 failedChecks가 빈 배열이다", () => {
    const summary = summarizeReadiness(parseReadiness("not an object"));
    expect(summary).toEqual({ status: "unknown", failedChecks: [] });
  });
});
