import { ApiError } from "@aios/api-client";
import { classifyIdempotencyFailure } from "@aios/shared-types";
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
        // task-383: INTEGRITY_IDEMPOTENCY_CONFLICT(new_key)/VALIDATION_IDEMPOTENCY_KEY_REQUIRED
        // (missing_header) 둘 다 400/409 범위라 discardKey는 isNonRetryableFailure로 이미
        // 처리되지만, missing_header는 클라이언트가 헤더 자체를 안 보낸 개발 결함이므로
        // 콘솔 경고를 남겨 조용히 묻히지 않게 한다. 두 경우 모두 여기서 자동 재제출은
        // 하지 않는다(new_key: 중복 결제 위험 — 사용자 확인 후 재시도만 허용).
        const failureKind = classifyIdempotencyFailure(err);
        if (failureKind === "missing_header") {
          console.warn(
            "[useIdempotentSubmit] Idempotency-Key 헤더 없이 요청됨(클라이언트 결함) — 재시도해도 같은 결과입니다.",
            err,
          );
        }
        if (isNonRetryableFailure(err) || failureKind !== "none") {
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
