import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useFieldErrors } from "./useFieldErrors";

describe("useFieldErrors", () => {
  it("VALIDATION_ 접두 error_code면 details.fields[]를 필드별 오류 맵으로 세팅한다", () => {
    const { result } = renderHook(() => useFieldErrors());

    act(() => {
      result.current.setFromError({
        error_code: "VALIDATION_INVALID_FIELD",
        message: "요청 값이 올바르지 않습니다.",
        details: { fields: ["body.email", "body.amount"] },
      });
    });

    expect(result.current.fieldErrors).toEqual({
      email: "요청 값이 올바르지 않습니다.",
      amount: "요청 값이 올바르지 않습니다.",
    });
  });

  it("negative: 미지의(비-VALIDATION_) error_code는 빈 맵을 세팅한다", () => {
    const { result } = renderHook(() => useFieldErrors());

    act(() => {
      result.current.setFromError({
        error_code: "STATE_INVALID_TRANSITION",
        message: "현재 상태에서는 수행할 수 없는 작업입니다.",
        details: { fields: ["body.status"] },
      });
    });

    expect(result.current.fieldErrors).toEqual({});
  });

  it("negative: details 형식이 배열이 아니면 throw 없이 빈 맵을 세팅한다", () => {
    const { result } = renderHook(() => useFieldErrors());

    expect(() => {
      act(() => {
        result.current.setFromError({
          error_code: "VALIDATION_INVALID_FIELD",
          message: "요청 값이 올바르지 않습니다.",
          details: { fields: "body.email" },
        });
      });
    }).not.toThrow();

    expect(result.current.fieldErrors).toEqual({});
  });

  it("필드를 수정하면 해당 필드 오류만 사라지고 나머지는 유지된다", () => {
    const { result } = renderHook(() => useFieldErrors());

    act(() => {
      result.current.setFromError({
        error_code: "VALIDATION_INVALID_FIELD",
        message: "요청 값이 올바르지 않습니다.",
        details: { fields: ["body.email", "body.amount"] },
      });
    });

    act(() => {
      result.current.clearField("email");
    });

    expect(result.current.fieldErrors).toEqual({
      amount: "요청 값이 올바르지 않습니다.",
    });
  });
});
