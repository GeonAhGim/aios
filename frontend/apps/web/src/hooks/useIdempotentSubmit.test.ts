import { ApiError } from "@aios/api-client";
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DuplicateSubmitError, useIdempotentSubmit } from "./useIdempotentSubmit";

describe("useIdempotentSubmit", () => {
  it("5xx 실패 후 재시도하면 같은 키를 재사용한다", async () => {
    const { result } = renderHook(() => useIdempotentSubmit("wallet.topup:req-1"));
    const seenKeys: string[] = [];

    await expect(
      result.current.submit(async (key) => {
        seenKeys.push(key);
        throw new ApiError(503, "일시적 오류");
      }),
    ).rejects.toThrow(ApiError);

    await expect(
      result.current.submit(async (key) => {
        seenKeys.push(key);
        throw new ApiError(503, "일시적 오류");
      }),
    ).rejects.toThrow(ApiError);

    expect(seenKeys).toHaveLength(2);
    expect(seenKeys[1]).toBe(seenKeys[0]);
  });

  it("네트워크 오류(ApiError 아님)도 같은 키로 재시도한다", async () => {
    const { result } = renderHook(() => useIdempotentSubmit("executions.start:exec-1"));
    const seenKeys: string[] = [];

    await expect(
      result.current.submit(async (key) => {
        seenKeys.push(key);
        throw new TypeError("Failed to fetch");
      }),
    ).rejects.toThrow(TypeError);

    await expect(
      result.current.submit(async (key) => {
        seenKeys.push(key);
        return key;
      }),
    ).resolves.toBe(seenKeys[0]);

    expect(seenKeys).toHaveLength(2);
    expect(seenKeys[1]).toBe(seenKeys[0]);
  });

  it("negative: 재시도 불가한 4xx(검증/권한) 실패 후에는 새 키를 받는다", async () => {
    const { result } = renderHook(() => useIdempotentSubmit("marketplace.purchase:listing-42"));
    const seenKeys: string[] = [];

    await expect(
      result.current.submit(async (key) => {
        seenKeys.push(key);
        throw new ApiError(400, "위험등급 동의가 필요합니다.");
      }),
    ).rejects.toThrow(ApiError);

    await result.current.submit(async (key) => {
      seenKeys.push(key);
      return key;
    });

    expect(seenKeys).toHaveLength(2);
    expect(seenKeys[1]).not.toBe(seenKeys[0]);
  });

  it("negative: 성공 후 재클릭이 이전 키를 재사용하면 실패한다(새 키를 받아야 한다)", async () => {
    const { result } = renderHook(() => useIdempotentSubmit("executions.create"));
    const seenKeys: string[] = [];

    await result.current.submit(async (key) => {
      seenKeys.push(key);
      return key;
    });
    await result.current.submit(async (key) => {
      seenKeys.push(key);
      return key;
    });

    expect(seenKeys).toHaveLength(2);
    expect(seenKeys[1]).not.toBe(seenKeys[0]);
  });

  it("in-flight 요청이 있는 동안 중복 제출은 거부되고 원 요청은 그대로 진행된다", async () => {
    const { result } = renderHook(() => useIdempotentSubmit("marketplace.purchase:listing-7"));

    let resolveFirst!: () => void;
    const first = result.current.submit(
      (key) =>
        new Promise<string>((resolve) => {
          resolveFirst = () => resolve(key);
        }),
    );

    await expect(
      result.current.submit(async (key) => key),
    ).rejects.toBeInstanceOf(DuplicateSubmitError);

    resolveFirst();
    await expect(first).resolves.toEqual(expect.any(String));
  });

  // task-383: INTEGRITY_IDEMPOTENCY_CONFLICT(409) → new_key
  it("409 INTEGRITY_IDEMPOTENCY_CONFLICT는 discardKey를 호출하고 자동 재제출하지 않는다", async () => {
    const { result } = renderHook(() => useIdempotentSubmit("wallet.topup:req-conflict"));
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const seenKeys: string[] = [];
    const call = vi.fn(async (key: string) => {
      seenKeys.push(key);
      throw new ApiError(409, "이미 처리된 요청입니다.", undefined, "INTEGRITY_IDEMPOTENCY_CONFLICT");
    });

    await expect(result.current.submit(call)).rejects.toThrow(ApiError);

    expect(call).toHaveBeenCalledTimes(1); // 자동 재제출 없음
    expect(seenKeys).toHaveLength(1);
    expect(warnSpy).not.toHaveBeenCalled(); // missing_header 전용 경고는 여기서 뜨지 않음
    warnSpy.mockRestore();
  });

  it("409 INTEGRITY_IDEMPOTENCY_CONFLICT 후 사용자가 재시도하면 새 키를 받는다", async () => {
    const { result } = renderHook(() => useIdempotentSubmit("wallet.topup:req-conflict-2"));
    const seenKeys: string[] = [];

    await expect(
      result.current.submit(async (key) => {
        seenKeys.push(key);
        throw new ApiError(409, "이미 처리된 요청입니다.", undefined, "INTEGRITY_IDEMPOTENCY_CONFLICT");
      }),
    ).rejects.toThrow(ApiError);

    await result.current.submit(async (key) => {
      seenKeys.push(key);
      return key;
    });

    expect(seenKeys).toHaveLength(2);
    expect(seenKeys[1]).not.toBe(seenKeys[0]);
  });

  // task-383: VALIDATION_IDEMPOTENCY_KEY_REQUIRED(400) → missing_header
  it("400 VALIDATION_IDEMPOTENCY_KEY_REQUIRED는 콘솔 경고만 남기고 재시도하지 않는다", async () => {
    const { result } = renderHook(() => useIdempotentSubmit("executions.create:req-missing-header"));
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const call = vi.fn(async () => {
      throw new ApiError(400, "요청이 올바르지 않습니다.", undefined, "VALIDATION_IDEMPOTENCY_KEY_REQUIRED");
    });

    await expect(result.current.submit(call)).rejects.toThrow(ApiError);

    expect(call).toHaveBeenCalledTimes(1); // 재시도 없음
    expect(warnSpy).toHaveBeenCalledTimes(1);
    warnSpy.mockRestore();
  });

  // 회귀: idempotency 실패 분류 도입 후에도 성공 경로의 키 폐기 동작은 그대로다.
  it("회귀: 성공 경로는 그대로 키를 폐기하고 다음 제출은 새 키를 받는다", async () => {
    const { result } = renderHook(() => useIdempotentSubmit("executions.create:req-success"));
    const seenKeys: string[] = [];

    await result.current.submit(async (key) => {
      seenKeys.push(key);
      return key;
    });
    await result.current.submit(async (key) => {
      seenKeys.push(key);
      return key;
    });

    expect(seenKeys).toHaveLength(2);
    expect(seenKeys[1]).not.toBe(seenKeys[0]);
  });
});
