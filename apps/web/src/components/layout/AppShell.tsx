import { useLogout, useMe } from "@aios/shared-hooks";
import { cn } from "@aios/ui-web";
import type { ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

const NAV_ITEMS = [
  { to: "/dashboard", label: "대시보드" },
  { to: "/exchanges", label: "거래소" },
  { to: "/strategy-builder", label: "전략편집기" },
  { to: "/marketplace", label: "마켓플레이스" },
  { to: "/executions", label: "실행제어판" },
  { to: "/portfolio", label: "포트폴리오" },
  { to: "/reports", label: "보고서" },
  { to: "/wallet", label: "지갑" },
];

function Logo() {
  return (
    <Link to="/dashboard" className="flex items-center gap-2 text-fg">
      <span className="flex h-7 w-7 items-center justify-center rounded-md bg-accent text-sm font-bold text-white">
        A
      </span>
      <span className="text-base font-semibold tracking-tight">AIOS</span>
    </Link>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { data: me } = useMe();
  const logout = useLogout();
  const navigate = useNavigate();
  const location = useLocation();

  return (
    <div className="min-h-screen bg-bg text-fg">
      <header className="border-b border-border">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-y-2 px-6 py-3">
          <Logo />
          <nav className="flex flex-wrap items-center gap-1 text-sm">
            {NAV_ITEMS.map((item) => {
              const isActive = location.pathname.startsWith(item.to);
              return (
                <Link
                  key={item.to}
                  to={item.to}
                  className={cn(
                    "rounded-md px-3 py-1.5 transition-colors",
                    isActive
                      ? "bg-accent-muted text-accent-hover"
                      : "text-fg-secondary hover:bg-surface-hover hover:text-fg",
                  )}
                >
                  {item.label}
                </Link>
              );
            })}
            <Link
              to="/settings/approval"
              className={cn(
                "rounded-md px-3 py-1.5 transition-colors",
                location.pathname.startsWith("/settings")
                  ? "bg-accent-muted text-accent-hover"
                  : "text-fg-secondary hover:bg-surface-hover hover:text-fg",
              )}
            >
              설정
            </Link>
            {me?.isPlatformAdmin && (
              <Link
                to="/admin"
                className={cn(
                  "rounded-md px-3 py-1.5 transition-colors",
                  location.pathname.startsWith("/admin")
                    ? "bg-warning-muted text-warning"
                    : "text-warning/80 hover:bg-warning-muted hover:text-warning",
                )}
              >
                관리자
              </Link>
            )}
          </nav>
          <div className="flex items-center gap-3 text-sm">
            <span className="text-fg-muted">{me?.email}</span>
            <button
              type="button"
              onClick={() => {
                logout();
                navigate("/login");
              }}
              className="rounded-md border border-border-strong px-3 py-1.5 text-fg-secondary transition-colors hover:bg-surface-hover hover:text-fg"
            >
              로그아웃
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-8">{children}</main>
    </div>
  );
}
