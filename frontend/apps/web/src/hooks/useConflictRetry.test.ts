import { ApiError } from "@aios/api-client";
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useConflictRetry } from "./useConflictRetry";

describe("useConflictRetry", () => {
  it("409 STATE_CONCURRENCY_CONFLICT는 재조회 후 1회 재시도해서 성공한다", async () => {
    const refetch = vi.fn().mockResolvedValue(undefined);
    let calls = 0;
    const mutate = vi.fn(async () => {
      calls += 1;
      if (calls === 1) throw new ApiError(409, "충돌", undefined, "STATE_CONCURRENCY_CONFLICT");
      return "ok";
    });
    const { result } = renderHook(() => useConflictRetry(mutate, refetch));

    await expect(result.current.run()).resolves.toBe("ok");
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(mutate).toHaveBeenCalledTimes(2);
  });

  it("재조회 후에도 409가 반복되면 두 번째 실패는 그대로 던진다(재시도 상한 1회)", async () => {
    const refetch = vi.fn().mockResolvedValue(undefined);
    const mutate = vi.fn(async () => {
      throw new ApiError(409, "충돌", undefined, "STATE_CONCURRENCY_CONFLICT");
    });
    const { result } = renderHook(() => useConflictRetry(mutate, refetch));

    await expect(result.current.run()).rejects.toMatchObject({
      errorCode: "STATE_CONCURRENCY_CONFLICT",
    });
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(mutate).toHaveBeenCalledTimes(2);
  });

  it("409 STATE_INVALID_TRANSITION은 재시도 없이 즉시 던진다", async () => {
    const refetch = vi.fn().mockResolvedValue(undefined);
    const mutate = vi.fn(async () => {
      throw new ApiError(409, "잘못된 전이", undefined, "STATE_INVALID_TRANSITION");
    });
    const { result } = renderHook(() => useConflictRetry(mutate, refetch));

    await expect(result.current.run()).rejects.toMatchObject({
      errorCode: "STATE_INVALID_TRANSITION",
    });
    expect(refetch).not.toHaveBeenCalled();
    expect(mutate).toHaveBeenCalledTimes(1);
  });

  it("409가 아닌 실패는 재시도 없이 즉시 던진다", async () => {
    const refetch = vi.fn().mockResolvedValue(undefined);
    const mutate = vi.fn(async () => {
      throw new ApiError(500, "서버 오류", undefined, "INTERNAL_ERROR");
    });
    const { result } = renderHook(() => useConflictRetry(mutate, refetch));

    await expect(result.current.run()).rejects.toMatchObject({ errorCode: "INTERNAL_ERROR" });
    expect(refetch).not.toHaveBeenCalled();
    expect(mutate).toHaveBeenCalledTimes(1);
  });
});
