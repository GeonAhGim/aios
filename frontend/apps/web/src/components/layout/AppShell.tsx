import { useLogout, useMe } from "@aios/shared-hooks";
import { cn } from "@aios/ui-web";
import type { ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ADMIN_NAV_ITEMS, MAIN_NAV_ITEMS, SETTINGS_NAV_ITEMS, type NavItem } from "./navItems";

function NavLink({
  item,
  pathname,
  activeClassName,
}: {
  item: NavItem;
  pathname: string;
  activeClassName: string;
}) {
  const isActive = pathname.startsWith(item.to);
  return (
    <Link
      to={item.to}
      className={cn(
        "rounded-md px-3 py-1.5 text-sm transition-colors",
        isActive ? activeClassName : "text-fg-secondary hover:bg-surface-hover hover:text-fg",
      )}
    >
      {item.label}
    </Link>
  );
}

function Logo() {
  return (
    <Link to="/dashboard" className="flex items-center gap-2 text-fg">
      <span className="flex h-7 w-7 items-center justify-center rounded-md bg-accent text-sm font-bold text-bg">
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
          <nav className="flex flex-wrap items-center gap-1">
            {MAIN_NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                item={item}
                pathname={location.pathname}
                activeClassName="bg-accent-muted text-accent-hover"
              />
            ))}
            <span className="mx-1 h-4 w-px bg-border-strong" aria-hidden="true" />
            {SETTINGS_NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                item={item}
                pathname={location.pathname}
                activeClassName="bg-accent-muted text-accent-hover"
              />
            ))}
            {me?.isPlatformAdmin && (
              <>
                <span className="mx-1 h-4 w-px bg-border-strong" aria-hidden="true" />
                {ADMIN_NAV_ITEMS.map((item) => (
                  <NavLink
                    key={item.to}
                    item={item}
                    pathname={location.pathname}
                    activeClassName="bg-warning-muted text-warning"
                  />
                ))}
              </>
            )}
          </nav>
          <div className="flex items-center gap-3 text-sm">
            <span className="text-fg-muted">{me?.email}</span>
            <button
              type="button"
              onClick={() => {
                // 사용자가 직접 누른 로그아웃이라 next 복귀가 필요 없다 — 세션
                // 만료로 인한 자동 로그아웃(task-354)은 useAuthStore의 401
                // 핸들러가 처리하고, ProtectedRoute가 next를 붙여 리다이렉트한다.
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
