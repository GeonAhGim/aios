import { describe, expect, it } from "vitest";
import { parseValidationResultView } from "./validationView";

const RESULT = {
  run_id: "run-1",
  strategy_id: "my-strategy",
  strategy_version: "1.0.0",
  check_type: "BACKTEST",
  state: "SUCCEEDED",
  outcome: "PASS",
  metrics: { sharpe: 1.2 },
  warnings: [],
  hard_fail_reasons: [],
  obligations: [],
  result_hash: "hash-1",
  created_at: "2026-09-08T00:00:00Z",
  schema_version: "v1",
};

describe("parseValidationResultView", () => {
  it("§4 필드를 그대로 보존한다", () => {
    expect(parseValidationResultView(RESULT)).toEqual(RESULT);
  });

  it("ApiResponse 봉투({data})로 감싼 응답도 파싱한다", () => {
    expect(parseValidationResultView({ data: RESULT, meta: { trace_id: "t1" } })).toEqual(RESULT);
  });

  it("outcome이 null(QUEUED/RUNNING 등 미판정)이어도 유효하다", () => {
    const pending = { ...RESULT, state: "RUNNING", outcome: null };
    expect(parseValidationResultView(pending)).toEqual(pending);
  });

  it("negative: 필드 누락(run_id 없음)이면 null이다", () => {
    const { run_id: _drop, ...missing } = RESULT;
    expect(parseValidationResultView(missing)).toBeNull();
  });

  it("result_hash는 null이어도(아직 계산 전) 유효하다", () => {
    const noHash = { ...RESULT, result_hash: null };
    expect(parseValidationResultView(noHash)).toEqual(noHash);
  });

  it("negative: 잘못된 state이면 null이다", () => {
    expect(parseValidationResultView({ ...RESULT, state: "UNKNOWN_STATE" })).toBeNull();
  });

  it("negative: 잘못된 outcome이면 null이다", () => {
    expect(parseValidationResultView({ ...RESULT, outcome: "MAYBE" })).toBeNull();
  });

  it("negative: warnings 안에 문자열이 아닌 값이 섞이면 null이다", () => {
    expect(parseValidationResultView({ ...RESULT, warnings: ["ok", 1] })).toBeNull();
  });

  it("negative: schema_version이 v1이 아니면 null이다(무음 통과 금지)", () => {
    expect(parseValidationResultView({ ...RESULT, schema_version: "v2" })).toBeNull();
  });

  it("negative: schema_version 필드가 아예 없으면 null이다", () => {
    const { schema_version: _drop, ...withoutVersion } = RESULT;
    expect(parseValidationResultView(withoutVersion)).toBeNull();
  });

  it("negative: 응답이 없으면(null/undefined) null이고 예외를 던지지 않는다", () => {
    expect(() => parseValidationResultView(null)).not.toThrow();
    expect(parseValidationResultView(null)).toBeNull();
    expect(parseValidationResultView(undefined)).toBeNull();
  });
});
