import { ApiError } from "@aios/api-client";
import { classifyBadRequest, classifyForbidden, routeApiError } from "@aios/shared-types";
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";

// task-2620(H-2 CM-17 프론트): spec §3.3 에러 taxonomy — MandateActionError와
// 동일 관용(각 라우트가 자기 에러 표면을 소유한다, task-2336 decision). err.message를
// 직접 노출하지 않고 routeApiError로 판정해 400/403/그 외를 BadRequestNotice/
// ForbiddenNotice/ErrorMessage 경로로만 보여준다.
export function ComplianceActionError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (classifyBadRequest(error)) return <BadRequestNotice error={error} />;
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      onRetry={onRetry}
    />
  );
}
