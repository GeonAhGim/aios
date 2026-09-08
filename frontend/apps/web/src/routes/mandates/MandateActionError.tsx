import { ApiError } from "@aios/api-client";
import { classifyBadRequest, classifyForbidden, routeApiError } from "@aios/shared-types";
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";

// task-2336(FE-OPS-2): spec §3.3 에러 taxonomy — 이 화면의 모든 실패는 err.message를
// 직접 노출하지 않고 routeApiError로 판정해 400/403/그 외를 BadRequestNotice/
// ForbiddenNotice/ErrorMessage 경로로만 보여준다(AlertsPage 패턴). CM-5(작성자=승인자)
// 위반은 VALIDATION_INVALID_FIELD(400, details.fields 없음)로 매핑되므로
// BadRequestNotice의 "field 갈래 fallback 배너"가 거부 사유(서버 message)를 그대로
// 노출한다 — 승인 버튼을 비활성화하지 않는다(DoD b).
export function MandateActionError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
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
