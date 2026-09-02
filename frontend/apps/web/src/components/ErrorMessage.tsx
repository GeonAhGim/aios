import { useEffect, useState } from "react";
import { classifyRetry, getApiErrorMessage } from "@aios/shared-types";
import { Alert, Button } from "@aios/ui-web";

// ApiError(§3.3 ApiError 봉투)의 error_code를 사용자용 한국어 메시지로 바꿔
// 보여주는 표시 전용 컴포넌트. 매핑 로직 자체는 shared-types/apiError.ts에
// 있으므로 여기서는 UI 배치만 담당한다.
//
// spec §3.3 재시도 열: classifyRetry(kind)가 "none"이 아닐 때만(refetch/backoff)
// 재시도 버튼을 보여준다. "backoff"(예: RATE_LIMIT_EXCEEDED)는 retryAfterSec을
// 초 단위로 카운트다운하고, 0이 되면(또는 애초에 값이 없으면) 버튼을 활성화한다.
// "refetch"(STATE_CONCURRENCY_CONFLICT)는 대기 없이 즉시 활성화된다. 버튼 클릭 시
// 실제 재시도는 호출부 책임(useRetryableAction 등) — 이 컴포넌트는 표시만 한다.
interface ErrorMessageProps {
  errorCode?: string | null;
  message?: string | null;
  traceId?: string | null;
  retryAfterSec?: number | null;
  onRetry?: () => void;
  // useFieldErrors가 뽑아낸 필드별 오류 맵(VALIDATION_INVALID_FIELD의 details.fields[]).
  // 값이 있으면 각 필드 옆에 이미 개별 오류가 표시된다는 뜻이므로, 이 배너는
  // 중복 안내를 피하기 위해 렌더링하지 않는다.
  fieldErrors?: Record<string, string>;
}

export function ErrorMessage({
  errorCode,
  message,
  traceId,
  retryAfterSec,
  onRetry,
  fieldErrors,
}: ErrorMessageProps) {
  const text = getApiErrorMessage(errorCode, message);
  const hasMappedFieldErrors = Boolean(fieldErrors && Object.keys(fieldErrors).length > 0);
  const classification = classifyRetry({ errorCode, retryAfterSec });
  const canRetry = classification.kind !== "none";
  const isBackoff = classification.kind === "backoff";
  const [remainingSec, setRemainingSec] = useState(
    retryAfterSec && retryAfterSec > 0 ? retryAfterSec : 0,
  );

  useEffect(() => {
    setRemainingSec(retryAfterSec && retryAfterSec > 0 ? retryAfterSec : 0);
  }, [retryAfterSec, errorCode]);

  useEffect(() => {
    if (!isBackoff || remainingSec <= 0) return;
    const timer = setTimeout(() => setRemainingSec((prev) => Math.max(0, prev - 1)), 1000);
    return () => clearTimeout(timer);
  }, [isBackoff, remainingSec]);

  if (hasMappedFieldErrors) return null;

  return (
    <Alert tone="danger">
      <p>{text}</p>
      {traceId && <p className="mt-1 text-xs text-fg-muted">지원코드: {traceId}</p>}
      {canRetry && onRetry && (
        <div className="mt-2 flex items-center gap-2">
          {remainingSec > 0 && (
            <span className="text-xs text-fg-muted">{remainingSec}초 후 재시도 가능</span>
          )}
          <Button size="sm" variant="secondary" disabled={remainingSec > 0} onClick={onRetry}>
            다시 시도
          </Button>
        </div>
      )}
    </Alert>
  );
}
