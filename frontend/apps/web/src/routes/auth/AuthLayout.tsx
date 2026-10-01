import type { ReactNode } from "react";
import { Link } from "react-router-dom";

// F-2(task-10642, UX_JOURNEYS.md §6 J1 사용감 소견): 가입 → MFA → 위험성향평가는
// 전진 전용이라 앞 단계 실수(예: 이메일 오타)를 되돌릴 수 없었다. backTo가 있으면
// 각 온보딩 단계 상단에 이전 단계로 돌아가는 링크를 노출한다 — 단계 수 자체는
// 늘리지 않는다(기존 라우트로의 네비게이션일 뿐).
export function AuthLayout({
  title,
  subtitle,
  backTo,
  backLabel,
  children,
}: {
  title: string;
  subtitle?: string;
  backTo?: string;
  backLabel?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-4">
      <div className="w-full max-w-sm">
        {backTo && (
          <Link to={backTo} className="mb-4 inline-block text-sm text-accent-hover hover:underline">
            {backLabel}
          </Link>
        )}
        <div className="mb-8 flex flex-col items-center gap-3">
          <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-accent text-lg font-bold text-bg">
            A
          </span>
          <div className="text-center">
            <h1 className="text-xl font-semibold text-fg">{title}</h1>
            {subtitle && <p className="mt-1 text-sm text-fg-muted">{subtitle}</p>}
          </div>
        </div>
        <div className="rounded-xl border border-border bg-surface p-6">{children}</div>
      </div>
    </div>
  );
}
