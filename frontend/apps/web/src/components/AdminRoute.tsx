import { useMe } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyForbidden, routeApiError } from "@aios/shared-types";
import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { ErrorMessage } from "./ErrorMessage";
import { ForbiddenNotice } from "./ForbiddenNotice";

// FD-18 — is_platform_admin 가드.
export function AdminRoute({ children }: { children: ReactNode }) {
  const { data: me, isLoading, isError, error, refetch } = useMe();

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-bg text-fg-muted">
        로딩 중...
      </div>
    );
  }
  // task-1155: useMe()가 네트워크/서버 오류로 실패하면 이전에는 me가 undefined인
  // 채로 아래 !me?.isPlatformAdmin 분기를 타 아무 안내 없이 /dashboard로
  // 리다이렉트했다(관리자 홈 진입 실패가 조용히 삼켜짐, spec §3.3 위반). isError를
  // 먼저 갈라 routeApiError(task-483)+ErrorMessage/ForbiddenNotice 경로로 보여준다.
  if (isError) {
    const routed = routeApiError(error);
    return (
      <div className="flex h-screen items-center justify-center bg-bg p-6">
        <div className="w-full max-w-md">
          {classifyForbidden(error) ? (
            <ForbiddenNotice error={error} />
          ) : (
            <ErrorMessage
              errorCode={error instanceof ApiError ? error.errorCode : undefined}
              message={error instanceof Error ? error.message : undefined}
              traceId={error instanceof ApiError ? error.traceId : undefined}
              retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
              onRetry={() => refetch()}
            />
          )}
        </div>
      </div>
    );
  }
  if (!me?.isPlatformAdmin) {
    return <Navigate to="/dashboard" replace />;
  }
  return <>{children}</>;
}
