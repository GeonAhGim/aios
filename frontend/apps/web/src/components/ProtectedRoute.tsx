import { useMe, useRiskProfile, useAuthStore } from "@aios/shared-hooks";
import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

// 17번 문서 §17.3 온보딩 가드 — 로그인 -> MFA 설정 -> 적합성평가 순서를
// 예외 없이 강제한다(정책문서 §4.10 "MFA는 사용자 레벨에서도 예외 없이 강제",
// FD-15.1 "회원가입 직후 필수, 스킵 불가").
export function ProtectedRoute({ children }: { children: ReactNode }) {
  const token = useAuthStore((s) => s.token);
  const location = useLocation();
  const { data: me, isLoading: meLoading } = useMe();
  const { data: riskProfile, isLoading: riskLoading } = useRiskProfile();

  if (!token) {
    return <Navigate to="/login" replace />;
  }
  if (meLoading || riskLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-bg text-fg-muted">
        로딩 중...
      </div>
    );
  }
  if (!me) {
    return <Navigate to="/login" replace />;
  }
  if (!me.mfaEnabled && location.pathname !== "/onboarding/mfa-setup") {
    return <Navigate to="/onboarding/mfa-setup" replace />;
  }
  if (
    me.mfaEnabled &&
    !riskProfile &&
    location.pathname !== "/onboarding/risk-assessment"
  ) {
    return <Navigate to="/onboarding/risk-assessment" replace />;
  }
  return <>{children}</>;
}
