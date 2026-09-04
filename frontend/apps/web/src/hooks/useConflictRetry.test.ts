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

  // task-1308: run(...args)이 재시도에도 원호출과 같은 인자를 넘겨야 한다 — 호출부가
  // 클로저로 대상을 캡처해 공유 ref 우회를 없앨 수 있는 근거.
  it("run(arg)은 재시도 mutate에도 같은 arg를 넘긴다", async () => {
    const refetch = vi.fn().mockResolvedValue(undefined);
    let calls = 0;
    const mutate = vi.fn(async (target: string) => {
      calls += 1;
      if (calls === 1) throw new ApiError(409, "충돌", undefined, "STATE_CONCURRENCY_CONFLICT");
      return target;
    });
    const { result } = renderHook(() => useConflictRetry(mutate, refetch));

    await expect(result.current.run("A")).resolves.toBe("A");
    expect(mutate).toHaveBeenNthCalledWith(1, "A");
    expect(mutate).toHaveBeenNthCalledWith(2, "A");
  });

  it("동시에 다른 인자로 run을 두 번 호출해도 각 호출은 자신의 인자로만 재시도한다", async () => {
    const refetch = vi.fn().mockResolvedValue(undefined);
    const attempts: Record<string, number> = {};
    const mutate = vi.fn(async (target: string) => {
      attempts[target] = (attempts[target] ?? 0) + 1;
      if (target === "A" && attempts[target] === 1) {
        throw new ApiError(409, "충돌", undefined, "STATE_CONCURRENCY_CONFLICT");
      }
      return target;
    });
    const { result } = renderHook(() => useConflictRetry(mutate, refetch));

    const runA = result.current.run("A");
    const runB = result.current.run("B");

    await expect(runA).resolves.toBe("A");
    await expect(runB).resolves.toBe("B");
    expect(mutate.mock.calls.map((c) => c[0])).toEqual(["A", "B", "A"]);
  });
});
