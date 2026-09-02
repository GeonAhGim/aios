import { classifyStateConflict } from "@aios/shared-types";
import { useCallback } from "react";

export interface UseConflictRetryResult<T> {
  run: () => Promise<T>;
}

/**
 * §3.3 STATE_CONCURRENCY_CONFLICT(409, 재시도 예)를 실제 재시도 동작으로 옮긴다.
 * classifyStateConflict(err)가 "refetch_retry"일 때만 refetch() 후 mutate()를 정확히
 * 1회 재시도한다. STATE_INVALID_TRANSITION·idempotency·409가 아닌 실패, 그리고 재시도
 * 후 다시 실패한 409는 그대로 던진다(무한 재시도 금지).
 */
export function useConflictRetry<T>(
  mutate: () => Promise<T>,
  refetch: () => Promise<unknown>,
): UseConflictRetryResult<T> {
  const run = useCallback(async (): Promise<T> => {
    try {
      return await mutate();
    } catch (err) {
      if (classifyStateConflict(err) !== "refetch_retry") throw err;
      await refetch();
      return await mutate();
    }
  }, [mutate, refetch]);

  return { run };
}
