import { getApiErrorMessage } from "@aios/shared-types";
import { Alert } from "@aios/ui-web";

// ApiError(§3.3 ApiError 봉투)의 error_code를 사용자용 한국어 메시지로 바꿔
// 보여주는 표시 전용 컴포넌트. 매핑 로직 자체는 shared-types/apiError.ts에
// 있으므로 여기서는 UI 배치만 담당한다.
interface ErrorMessageProps {
  errorCode?: string | null;
  message?: string | null;
  traceId?: string | null;
}

export function ErrorMessage({ errorCode, message, traceId }: ErrorMessageProps) {
  const text = getApiErrorMessage(errorCode, message);
  return (
    <Alert tone="danger">
      <p>{text}</p>
      {traceId && <p className="mt-1 text-xs text-fg-muted">지원코드: {traceId}</p>}
    </Alert>
  );
}
