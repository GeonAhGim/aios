import { useMe } from "@aios/shared-hooks";
import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";

// FD-18 — is_platform_admin 가드.
export function AdminRoute({ children }: { children: ReactNode }) {
  const { data: me, isLoading } = useMe();

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-bg text-fg-muted">
        로딩 중...
      </div>
    );
  }
  if (!me?.isPlatformAdmin) {
    return <Navigate to="/dashboard" replace />;
  }
  return <>{children}</>;
}
