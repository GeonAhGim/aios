import { ApiError } from "@aios/api-client";
import {
  classifyBadRequest,
  classifyForbidden,
  classifyServerError,
  redactSecret,
  routeApiError,
} from "@aios/shared-types";
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";

// spec §3.3 에러 taxonomy: err.message를 직접 노출하지 않고 routeApiError(task-483)
// 경유 BadRequestNotice/ForbiddenNotice/ErrorMessage로만 보여준다(task-901/910 패턴).
// task-473 §3.6: 등록 폼은 서버가 에러 메시지에 apiKey/apiSecret 원문을 반향할 수
// 있어(예: "invalid field api_secret=...") ErrorMessage의 fallbackMessage 경로에는
// redactSecret을 통과시킨다. BadRequestNotice/ForbiddenNotice는 알려진 error_code일
// 때만 고정 한국어 문구(EXACT_MESSAGES)를 쓰므로 비밀 반향 위험이 없다 — "unknown"
// (미지 코드 또는 코드 없음)만 계속 redactSecret 경로로 보낸다.
// classifyServerError(task-937)가 재시도 가능(5xx)으로 판정할 때만 onRetry를 넘긴다.
//
// task-943: VALIDATION_INVALID_FIELD는 classifyBadRequest가 "field"로 분류해
// badRequestKind !== "unknown"에 걸려 BadRequestNotice로 가는데, 그 컴포넌트는
// field 갈래에서 null을 렌더한다(task-364 설계) — 지금까지 이 경로는 완전히
// 조용했다. fieldErrors를 ErrorMessage에 넘겨 계약을 지키고, 실제 표시는 아래
// 입력 옆 Field.error로 한다.
export function RegisterCredentialError({
  error,
  onRetry,
  fieldErrors,
}: {
  error: unknown;
  onRetry: () => void;
  fieldErrors: Record<string, string>;
}) {
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const badRequestKind = classifyBadRequest(error);
  if (badRequestKind && badRequestKind !== "unknown") return <BadRequestNotice error={error} />;
  const routed = routeApiError(error);
  const serverError = classifyServerError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : null}
      message={error instanceof Error ? redactSecret(error.message) : null}
      traceId={error instanceof ApiError ? error.traceId : null}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      onRetry={serverError.kind === "retryable" ? onRetry : undefined}
      fieldErrors={fieldErrors}
    />
  );
}

export function RevokeCredentialError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const badRequestKind = classifyBadRequest(error);
  if (badRequestKind && badRequestKind !== "unknown") return <BadRequestNotice error={error} />;
  const routed = routeApiError(error);
  const serverError = classifyServerError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : null}
      message={error instanceof Error ? redactSecret(error.message) : null}
      traceId={error instanceof ApiError ? error.traceId : null}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      onRetry={serverError.kind === "retryable" ? onRetry : undefined}
    />
  );
}
