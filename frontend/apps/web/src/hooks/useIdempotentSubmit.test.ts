import { ApiError } from "@aios/api-client";
import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
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
});
