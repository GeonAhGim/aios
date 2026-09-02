import { describe, expect, it } from "vitest";
import { classifyServerError } from "./serverError";

describe("classifyServerError", () => {
  it("503 EXCHANGE_UNAVAILABLE은 retryable이고 retryAfterSec을 그대로 담는다", () => {
    expect(classifyServerError({ errorCode: "EXCHANGE_UNAVAILABLE", retryAfterSec: 5 })).toEqual({
      kind: "retryable",
      afterSec: 5,
    });
  });

  it("503 DEPENDENCY_NOT_READY는 retryable이고 retryAfterSec이 없으면 afterSec은 undefined다", () => {
    expect(classifyServerError({ errorCode: "DEPENDENCY_NOT_READY" })).toEqual({
      kind: "retryable",
      afterSec: undefined,
    });
  });

  it("502 EXCHANGE_FATAL은 fatal이고 traceId를 그대로 담는다", () => {
    expect(classifyServerError({ errorCode: "EXCHANGE_FATAL", traceId: "trace-1" })).toEqual({
      kind: "fatal",
      traceId: "trace-1",
    });
  });

  it("500 INTERNAL_ERROR는 fatal이고 traceId가 없으면 undefined다", () => {
    expect(classifyServerError({ errorCode: "INTERNAL_ERROR" })).toEqual({
      kind: "fatal",
      traceId: undefined,
    });
  });

  // negative: 이 4개 코드 밖의 에러는 재시도 가능/불가능을 임의로 짐작하지 않고
  // not_applicable로 수렴한다.
  it("이 4개 코드에 속하지 않는 에러는 not_applicable이다", () => {
    expect(classifyServerError({ errorCode: "AUTH_REQUIRED" })).toEqual({ kind: "not_applicable" });
    expect(classifyServerError({ errorCode: "RATE_LIMIT_EXCEEDED", retryAfterSec: 3 })).toEqual({
      kind: "not_applicable",
    });
  });

  // negative: errorCode가 없거나 이 모양이 아닌 값(null/문자열/일반 Error)은 모두
  // not_applicable — throw하지 않는다.
  it("errorCode가 없거나 형태가 다른 값은 throw 없이 not_applicable이다", () => {
    expect(classifyServerError(null)).toEqual({ kind: "not_applicable" });
    expect(classifyServerError(undefined)).toEqual({ kind: "not_applicable" });
    expect(classifyServerError("EXCHANGE_UNAVAILABLE")).toEqual({ kind: "not_applicable" });
    expect(classifyServerError({ errorCode: null })).toEqual({ kind: "not_applicable" });
    expect(classifyServerError(new Error("boom"))).toEqual({ kind: "not_applicable" });
  });
});
