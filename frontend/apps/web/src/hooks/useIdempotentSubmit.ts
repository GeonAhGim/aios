import { ApiError } from "@aios/api-client";
import { useCallback, useRef, useState } from "react";
import { createIdempotencyKeyManager, type IdempotencyKeyManager } from "../lib/idempotency";

// 버튼 연타로 React state 업데이트(예: mutation.isPending)를 기다리지 않고 동기적으로
// 두 번째 제출을 막기 위한 전용 에러 — 호출부는 조용히 무시하면 된다.
export class DuplicateSubmitError extends Error {
  constructor() {
    super("이미 처리 중인 요청이 있습니다.");
  }
}

// 재시도 불가로 간주하는 실패(검증·권한 등 4xx) — discardKey로 폐기해 다음 시도가
// 새 키를 받게 한다. ApiError가 아닌 실패(네트워크 오류·타임아웃)와 5xx는 재시도
// 가능하다고 보고 같은 키를 유지한다.
function isNonRetryableFailure(err: unknown): boolean {
  return err instanceof ApiError && err.statusCode >= 400 && err.statusCode < 500;
}

export interface UseIdempotentSubmitResult {
  submit: <T>(action: (idempotencyKey: string) => Promise<T>) => Promise<T>;
}

/**
 * 금전 라우트(spec §9 PLT-15) 제출의 Idempotency-Key 수명주기(task-151)를 관리한다.
 * requestKey별로 재시도 시 같은 키를 재사용하고, 성공 또는 재시도 불가한 4xx 실패
 * 후에는 키를 폐기한다. in-flight 가드로 같은 요청의 중복 제출(버튼 연타)을 막는다.
 */
export function useIdempotentSubmit(requestKey: string): UseIdempotentSubmitResult {
  const [manager] = useState<IdempotencyKeyManager>(() => createIdempotencyKeyManager());
  const inFlightRef = useRef(false);

  const submit = useCallback(
    async <T,>(action: (idempotencyKey: string) => Promise<T>): Promise<T> => {
      if (inFlightRef.current) {
        throw new DuplicateSubmitError();
      }
      inFlightRef.current = true;
      const key = manager.getOrCreateKey(requestKey);
      try {
        const result = await action(key);
        manager.discardKey(requestKey);
        return result;
      } catch (err) {
        if (isNonRetryableFailure(err)) {
          manager.discardKey(requestKey);
        }
        throw err;
      } finally {
        inFlightRef.current = false;
      }
    },
    [requestKey, manager],
  );

  return { submit };
}
