import { useCallback, useState, useSyncExternalStore } from "react";
import { RATE_LIMIT_ERROR_CODE, classifyRetry } from "@aios/shared-types";
import { createIdempotencyKeyManager, type IdempotencyKeyManager } from "../lib/idempotency";

// afterSec이 없는 backoff(EXCHANGE_UNAVAILABLE/DEPENDENCY_NOT_READY 등 서버가
// retry_after_seconds를 안 주는 경우)의 로컬 기본 스케줄 — spec §3.3은 "백오프"만
// 명시하고 구체 수치는 정하지 않으므로 1s→2s→4s, 상한 3회로 클라이언트가 정한다.
const BACKOFF_SCHEDULE_SEC = [1, 2, 4] as const;
const MAX_BACKOFF_RETRIES = BACKOFF_SCHEDULE_SEC.length;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// task-841(§9 PLT-25): run()은 429 RATE_LIMIT_EXCEEDED도 이미 자동으로
// 백오프·재시도하지만, 그 대기는 setTimeout 하나뿐이라 사용자가 상황을 알 길이
// 없고, 비활성(백그라운드) 탭에서는 브라우저가 타이머를 스로틀링해 실제 대기가
// 늘어질 수 있다. 이 모듈 전역 store는 RATE_LIMIT_EXCEEDED로 대기 중일 때만
// 상태를 발행해 앱 루트 1곳에 마운트된 RateLimitNotice(전역 배너, MfaStepUpDialog와
// 동일한 패턴)가 카운트다운과 "다시 시도" 버튼을 보여주게 한다. 버튼은
// interruptibleSleep의 남은 대기를 즉시 풀어(retryNow) run()의 재시도를
// 앞당긴다 — 실제 재시도 로직 자체는 재구현하지 않는다.
export interface RateLimitNoticeState {
  retryAfterSec: number;
  retryNow: () => void;
}

let rateLimitNotice: RateLimitNoticeState | null = null;
let rateLimitNoticeToken = 0;
const rateLimitListeners = new Set<() => void>();

function setRateLimitNotice(next: RateLimitNoticeState | null): void {
  rateLimitNotice = next;
  rateLimitListeners.forEach((listener) => listener());
}

function subscribeRateLimitNotice(listener: () => void): () => void {
  rateLimitListeners.add(listener);
  return () => rateLimitListeners.delete(listener);
}

function getRateLimitNoticeSnapshot(): RateLimitNoticeState | null {
  return rateLimitNotice;
}

/** RateLimitNotice(전역 배너)가 구독하는 훅. 동시에 여러 run()이 겹쳐도 배너는
 *  1개뿐이므로 가장 최근에 발행된 상태만 보여준다. */
export function useRateLimitNotice(): RateLimitNoticeState | null {
  return useSyncExternalStore(subscribeRateLimitNotice, getRateLimitNoticeSnapshot);
}

function isRateLimitError(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    (err as { errorCode?: unknown }).errorCode === RATE_LIMIT_ERROR_CODE
  );
}

// 일반 sleep과 동작이 같되(skip을 호출하지 않으면 기존 테스트 타이밍 그대로),
// skip()을 부르면 원래 setTimeout이 아직 안 끝났어도 즉시 resolve한다. 이미
// resolve된 프로미스에 나중에 진짜 타이머가 다시 resolve를 불러도 no-op이라 안전하다.
function interruptibleSleep(ms: number): { promise: Promise<void>; skip: () => void } {
  let resolveFn: (() => void) | undefined;
  const promise = new Promise<void>((resolve) => {
    resolveFn = resolve;
    setTimeout(resolve, ms);
  });
  return { promise, skip: () => resolveFn?.() };
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
      let myNoticeToken = 0;

      // 이 run() 호출이 마지막으로 발행한 전역 배너가 아직 그대로일 때만 지운다 —
      // 그새 다른 run()이 새 429를 발행했다면 그쪽 배너를 건드리지 않는다.
      const clearOwnNotice = () => {
        if (myNoticeToken !== 0 && rateLimitNoticeToken === myNoticeToken) {
          setRateLimitNotice(null);
        }
      };

      for (;;) {
        try {
          const result = await action(key);
          if (requestKey) manager.discardKey(requestKey);
          clearOwnNotice();
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
            if (isRateLimitError(err)) {
              myNoticeToken = ++rateLimitNoticeToken;
              const { promise, skip } = interruptibleSleep(waitSec * 1000);
              setRateLimitNotice({ retryAfterSec: waitSec, retryNow: skip });
              await promise;
              clearOwnNotice();
            } else {
              await sleep(waitSec * 1000);
            }
            continue;
          }

          clearOwnNotice();
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
