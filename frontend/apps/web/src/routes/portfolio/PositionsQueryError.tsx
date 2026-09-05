import { ApiError } from "@aios/api-client";
import { classifyForbidden, routeApiError } from "@aios/shared-types";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { NotFoundState } from "../../components/NotFoundState";

// task-1524(LB-19): positions 조회 실패를 §3.3 taxonomy 갈래로 그린다 — 기존 분류기
// (routeApiError/classifyForbidden)만 경유하고 새 분류기는 만들지 않는다.
//   404 RESOURCE_NOT_FOUND(타 테넌트 리소스 포함, positions.py docstring) → NotFoundState
//     ("재시도" 열이 "아니오"라 재시도 버튼 없음).
//   403(TENANT_MISMATCH·POLICY_DENIED·MFA) → ForbiddenNotice.
//   그 외(5xx·429 등) → ErrorMessage. refetch_retry/backoff_retry일 때만 onRetry 배선.
interface PositionsQueryErrorProps {
  error: unknown;
  notFoundTitle: string;
  onRetry?: () => void;
}

export function PositionsQueryError({ error, notFoundTitle, onRetry }: PositionsQueryErrorProps) {
  const routed = routeApiError(error);
  if (routed.kind === "not_found") {
    return <NotFoundState title={notFoundTitle} />;
  }
  if (classifyForbidden(error)) {
    return <ForbiddenNotice error={error} />;
  }
  const canRetry = routed.kind === "refetch_retry" || routed.kind === "backoff_retry";
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
