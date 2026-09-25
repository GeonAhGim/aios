import { AiRouteNotImplementedError, ApiError } from "@aios/api-client";
import { routeApiError } from "@aios/shared-types";
import { ErrorMessage } from "../../components/ErrorMessage";

// FollowPage.tsx(FollowErrorBanner)와 동일 관용 — 유령 경로 오류
// (AiRouteNotImplementedError)도 별도 렌더 분기를 만들지 않고 같은
// ErrorMessage(message= prop)로 흘려보내되, 재시도 버튼만 강제로 끈다(라우터가
// 없다는 사실은 재시도로 바뀌지 않는다). AiStudioPage.tsx의 4개 섹션 파일이 모두
// 공유한다.
export function AiErrorBanner({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const notImplemented = error instanceof AiRouteNotImplementedError;
  const routed = routeApiError(error);
  const canRetry = !notImplemented && (routed.kind === "refetch_retry" || routed.kind === "backoff_retry");
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      onRetry={canRetry ? onRetry : undefined}
    />
  );
}
