// L4_platform_observability_tenancy_api_v1.0.md §3.3 error taxonomy: RESOURCE_NOT_FOUND(404)는
// "재시도" 열이 "아니오"다. 즉 재시도 배너(ErrorMessage)가 아니라 "없음" 상태로 렌더해야 한다.
//
// retryable.ts와 동일한 이유로(이 패키지는 api-client에 의존하지 않는다 — 순환 의존 방지)
// ApiError 클래스 대신 덕타이핑으로 { errorCode, statusCode } 모양만 검사한다.

export interface NotFoundErrorLike {
  errorCode?: string | null;
  statusCode?: number | null;
}

function isNotFoundErrorLike(err: unknown): err is NotFoundErrorLike {
  return (
    typeof err === "object" &&
    err !== null &&
    ("errorCode" in err || "statusCode" in err)
  );
}

// errorCode가 RESOURCE_NOT_FOUND이거나 statusCode가 404일 때만 true. 그 외 에러·null·
// ApiError가 아닌 값(일반 Error 등)은 모두 false — throw하지 않는다.
export function isResourceNotFound(err: unknown): boolean {
  if (!isNotFoundErrorLike(err)) return false;
  if (err.errorCode === "RESOURCE_NOT_FOUND") return true;
  return err.statusCode === 404;
}
