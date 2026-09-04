import { classifyStateConflict } from "@aios/shared-types";
import { useCallback } from "react";

export interface UseConflictRetryResult<T, A extends unknown[]> {
  run: (...args: A) => Promise<T>;
}

/**
 * §3.3 STATE_CONCURRENCY_CONFLICT(409, 재시도 예)를 실제 재시도 동작으로 옮긴다.
 * classifyStateConflict(err)가 "refetch_retry"일 때만 refetch() 후 mutate()를 정확히
 * 1회 재시도한다. STATE_INVALID_TRANSITION·idempotency·409가 아닌 실패, 그리고 재시도
 * 후 다시 실패한 409는 그대로 던진다(무한 재시도 금지).
 *
 * mutate는 run(...args)로 넘긴 인자를 그대로 받는다 — 원호출과 재시도가 같은 값을
 * 쓰도록 args를 클로저에 캡처해 두 호출 사이에서 재평가되지 않게 한다(task-1308:
 * 호출부가 공유 ref로 대상을 넘기면 재시도 시점에 값이 바뀌어 있을 수 있다).
 */
export function useConflictRetry<T, A extends unknown[] = []>(
  mutate: (...args: A) => Promise<T>,
  refetch: () => Promise<unknown>,
): UseConflictRetryResult<T, A> {
  const run = useCallback(
    async (...args: A): Promise<T> => {
      try {
        return await mutate(...args);
      } catch (err) {
        if (classifyStateConflict(err) !== "refetch_retry") throw err;
        await refetch();
        return await mutate(...args);
      }
    },
    [mutate, refetch],
  );

  return { run };
}
