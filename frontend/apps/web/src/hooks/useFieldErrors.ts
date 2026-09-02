import { useCallback, useState } from "react";
import { extractFieldErrors } from "@aios/shared-types";

export interface UseFieldErrorsResult {
  fieldErrors: Record<string, string>;
  setFromError: (err: unknown) => void;
  clearField: (field: string) => void;
}

/**
 * 폼 제출 실패(§3.3 VALIDATION_INVALID_FIELD) 시 details.fields[]를 필드별 오류 맵으로
 * 세팅하고, 사용자가 해당 필드를 다시 수정하면 그 키만 지운다(나머지 필드 오류는 유지).
 * 매핑 로직 자체는 shared-types/fieldErrors.ts가 담당 — 여기는 폼 상태 관리만 한다.
 */
export function useFieldErrors(): UseFieldErrorsResult {
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const setFromError = useCallback((err: unknown) => {
    setFieldErrors(extractFieldErrors(err));
  }, []);

  const clearField = useCallback((field: string) => {
    setFieldErrors((prev) => {
      if (!(field in prev)) return prev;
      const next = { ...prev };
      delete next[field];
      return next;
    });
  }, []);

  return { fieldErrors, setFromError, clearField };
}
