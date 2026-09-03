import { useCallback, useState } from "react";
import { classifyRetry } from "@aios/shared-types";
import { createIdempotencyKeyManager, type IdempotencyKeyManager } from "../lib/idempotency";

// afterSec이 없는 backoff(EXCHANGE_UNAVAILABLE/DEPENDENCY_NOT_READY 등 서버가
// retry_after_seconds를 안 주는 경우)의 로컬 기본 스케줄 — spec §3.3은 "백오프"만
// 명시하고 구체 수치는 정하지 않으므로 1s→2s→4s, 상한 3회로 클라이언트가 정한다.
const BACKOFF_SCHEDULE_SEC = [1, 2, 4] as const;
const MAX_BACKOFF_RETRIES = BACKOFF_SCHEDULE_SEC.length;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export interface UseRetryableActionOptions {
  /** 금전 라우트(spec §9 PLT-15)면 요청 식별자를 준다 — action에 넘겨줄 Idempotency-Key를
   *  재시도 내내(refetch/backoff 모두) 동일하게 유지한다(task-151 getOrCreateKey 재사용,
   *  새 키 생성 금지). 주지 않으면 action은 항상 key=undefined로 호출된다. */
  requestKey?: string;
  /** STATE_CONCURRENCY_CONFLICT(refetch)일 때 재시도 직전에 1회 호출하는 최신 상태 재조회 콜백. */
  refetch?: () => Promise<unknown>;
}

export interface UseRetryableActionResult<T> {
  run: (action: (idempotencyKey: string | undefined) => Promise<T>) => Promise<T>;
}

/**
 * §3.3 에러 taxonomy의 재시도 열을 실제 재시도 동작으로 옮긴다.
 * classifyRetry(err)가 "refetch"면 refetch() 후 1회, "backoff"면 afterSec(없으면
 * 1s→2s→4s) 대기 후 최대 3회, "none"이면 즉시 던진다.
 */
export function useRetryableAction<T>(
  options: UseRetryableActionOptions = {},
): UseRetryableActionResult<T> {
  const { requestKey, refetch } = options;
  const [manager] = useState<IdempotencyKeyManager>(() => createIdempotencyKeyManager());

  const run = useCallback(
    async (action: (idempotencyKey: string | undefined) => Promise<T>): Promise<T> => {
      const key = requestKey ? manager.getOrCreateKey(requestKey) : undefined;
      let refetched = false;
      let backoffAttempt = 0;

      for (;;) {
        try {
          const result = await action(key);
          if (requestKey) manager.discardKey(requestKey);
          return result;
        } catch (err) {
          const classification = classifyRetry(err);

          if (classification.kind === "refetch" && !refetched) {
            refetched = true;
            await refetch?.();
            continue;
          }

          if (classification.kind === "backoff" && backoffAttempt < MAX_BACKOFF_RETRIES) {
            const waitSec = classification.afterSec ?? BACKOFF_SCHEDULE_SEC[backoffAttempt];
            backoffAttempt += 1;
            await sleep(waitSec * 1000);
            continue;
          }

          if (requestKey && classification.kind === "none") {
            manager.discardKey(requestKey);
          }
          throw err;
        }
      }
    },
    [manager, requestKey, refetch],
  );

  return { run };
}
