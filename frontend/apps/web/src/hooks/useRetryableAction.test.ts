import { ApiError } from "@aios/api-client";
import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useRetryableAction } from "./useRetryableAction";

describe("useRetryableAction", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("409 STATE_CONCURRENCY_CONFLICT는 재조회 후 1회만 재시도한다", async () => {
    const refetch = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useRetryableAction<string>({ refetch }));

    let calls = 0;
    const value = await result.current.run(async () => {
      calls += 1;
      if (calls === 1) throw new ApiError(409, "충돌", undefined, "STATE_CONCURRENCY_CONFLICT");
      return "ok";
    });

    expect(value).toBe("ok");
    expect(calls).toBe(2);
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("409가 재조회 후에도 반복되면 두 번째 실패는 그대로 던진다(1회 제한)", async () => {
    const refetch = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useRetryableAction<string>({ refetch }));

    let calls = 0;
    await expect(
      result.current.run(async () => {
        calls += 1;
        throw new ApiError(409, "충돌", undefined, "STATE_CONCURRENCY_CONFLICT");
      }),
    ).rejects.toMatchObject({ errorCode: "STATE_CONCURRENCY_CONFLICT" });

    expect(calls).toBe(2);
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("503 EXCHANGE_UNAVAILABLE은 백오프(기본 1s) 후 재시도한다", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useRetryableAction<string>());

    let calls = 0;
    const promise = result.current.run(async () => {
      calls += 1;
      if (calls === 1) throw new ApiError(503, "일시적 오류", undefined, "EXCHANGE_UNAVAILABLE");
      return "ok";
    });

    await vi.advanceTimersByTimeAsync(1000);
    await expect(promise).resolves.toBe("ok");
    expect(calls).toBe(2);
  });

  it("429 RATE_LIMIT_EXCEEDED는 기본 스케줄이 아니라 서버가 준 retryAfterSec만큼 대기한다", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useRetryableAction<string>());

    let calls = 0;
    const promise = result.current.run(async () => {
      calls += 1;
      if (calls === 1) {
        throw new ApiError(429, "과도한 요청", undefined, "RATE_LIMIT_EXCEEDED", 5);
      }
      return "ok";
    });

    await vi.advanceTimersByTimeAsync(1000);
    expect(calls).toBe(1);

    await vi.advanceTimersByTimeAsync(4000);
    await expect(promise).resolves.toBe("ok");
    expect(calls).toBe(2);
  });

  it("금전 라우트는 백오프 재시도에도 같은 Idempotency-Key를 재사용한다(새 키 생성 금지)", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() =>
      useRetryableAction<string | undefined>({ requestKey: "wallet.topup:req-1" }),
    );

    const seenKeys: (string | undefined)[] = [];
    const promise = result.current.run(async (key) => {
      seenKeys.push(key);
      if (seenKeys.length === 1) {
        throw new ApiError(503, "일시적 오류", undefined, "EXCHANGE_UNAVAILABLE");
      }
      return key;
    });

    await vi.advanceTimersByTimeAsync(1000);
    await promise;

    expect(seenKeys).toHaveLength(2);
    expect(seenKeys[0]).toBeDefined();
    expect(seenKeys[1]).toBe(seenKeys[0]);
  });

  it("503 EXCHANGE_UNAVAILABLE이 백오프 상한(3회)까지 반복되면 최종 실패를 그대로 던진다", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useRetryableAction<string>());

    let calls = 0;
    const promise = result.current.run(async () => {
      calls += 1;
      throw new ApiError(503, "일시적 오류", undefined, "EXCHANGE_UNAVAILABLE");
    });
    const assertion = expect(promise).rejects.toMatchObject({ errorCode: "EXCHANGE_UNAVAILABLE" });

    await vi.advanceTimersByTimeAsync(1000 + 2000 + 4000);
    await assertion;

    expect(calls).toBe(4);
  });

  it("409 STATE_INVALID_TRANSITION은 같은 409여도 재시도하지 않는다(task-414)", async () => {
    const refetch = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useRetryableAction<string>({ refetch }));

    let calls = 0;
    await expect(
      result.current.run(async () => {
        calls += 1;
        throw new ApiError(409, "잘못된 상태 전이", undefined, "STATE_INVALID_TRANSITION");
      }),
    ).rejects.toMatchObject({ errorCode: "STATE_INVALID_TRANSITION" });

    expect(calls).toBe(1);
    expect(refetch).not.toHaveBeenCalled();
  });

  it("INTERNAL_ERROR는 재시도 없이 즉시 던진다", async () => {
    const { result } = renderHook(() => useRetryableAction<string>());

    let calls = 0;
    await expect(
      result.current.run(async () => {
        calls += 1;
        throw new ApiError(500, "서버 오류", undefined, "INTERNAL_ERROR");
      }),
    ).rejects.toMatchObject({ errorCode: "INTERNAL_ERROR" });

    expect(calls).toBe(1);
  });
});
