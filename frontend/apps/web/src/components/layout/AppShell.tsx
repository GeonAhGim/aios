import { useLogout, useMe } from "@aios/shared-hooks";
import { cn, ThemeToggle } from "@aios/ui-web";
import { useEffect, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { CommandPalette } from "../../commandPalette/CommandPalette";
import { ADMIN_NAV_ITEMS, MAIN_NAV_ITEMS, SETTINGS_NAV_ITEMS, type NavItem } from "./navItems";
import { useTranslation } from "react-i18next";

// UX-21 task-2705: 44개(main 26 + settings 6 + admin 12)에 달하는 nav 항목을 md
// 미만 뷰포트에서 flex-wrap 그대로 두면 헤더가 페이지 콘텐츠보다 길어지는 모바일
// 레이아웃 회귀가 난다 — md 이상에서는 항상 펼치고, 그 미만에서는 햄버거 토글로
// 숨김/펼침을 전환한다(링크 DOM은 하나만 유지, 중복 렌더 없음).
const MOBILE_NAV_ID = "app-shell-mobile-nav";

interface NavLinkProps {
  item: NavItem;
  pathname: string;
  activeClassName: string;
  onNavigate: () => void;
}

function NavLink({ item, pathname, activeClassName, onNavigate }: NavLinkProps) {
  const { t } = useTranslation();
  const isActive = pathname.startsWith(item.to);
  return (
    <Link
      to={item.to}
      onClick={onNavigate}
      className={cn(
        "rounded-md px-3 py-1.5 text-sm transition-colors",
        isActive ? activeClassName : "text-fg-secondary hover:bg-surface-hover hover:text-fg",
      )}
    >
      {t(item.label as any)}
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
  const { t } = useTranslation();
  const { data: me } = useMe();
  const logout = useLogout();
  const navigate = useNavigate();
  const location = useLocation();
  const [isMenuOpen, setMenuOpen] = useState(false);

  // negative(회귀): 모바일 메뉴를 연 채로 링크를 눌러 다른 화면으로 넘어가면
  // 다음 화면 위에 열린 오버레이가 그대로 남아 콘텐츠를 가리는 회귀가 난다 —
  // 경로가 바뀔 때마다 닫는다.
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  const closeMenu = () => setMenuOpen(false);

  return (
    <div className="min-h-screen bg-bg text-fg">
      <header className="border-b border-border">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-y-2 px-4 py-3 sm:px-6">
          <div className="flex w-full items-center justify-between md:w-auto">
            <Logo />
            <button
              type="button"
              onClick={() => setMenuOpen((open) => !open)}
              aria-expanded={isMenuOpen}
              aria-controls={MOBILE_NAV_ID}
              aria-label={isMenuOpen ? t("legacy.appShell.t3") : t("legacy.appShell.t2")}
              className="rounded-md border border-border-strong px-2.5 py-1.5 text-fg-secondary transition-colors hover:bg-surface-hover hover:text-fg md:hidden"
            >
              <span aria-hidden="true">{isMenuOpen ? "✕" : "☰"}</span>
            </button>
          </div>
          <nav
            id={MOBILE_NAV_ID}
            className={cn(
              "w-full flex-col gap-1 md:flex md:w-auto md:flex-row md:flex-wrap md:items-center",
              isMenuOpen ? "flex" : "hidden",
            )}
          >
            {MAIN_NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                item={item}
                pathname={location.pathname}
                activeClassName="bg-accent-muted text-accent-hover"
                onNavigate={closeMenu}
              />
            ))}
            <span className="mx-1 hidden h-4 w-px bg-border-strong md:block" aria-hidden="true" />
            {SETTINGS_NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                item={item}
                pathname={location.pathname}
                activeClassName="bg-accent-muted text-accent-hover"
                onNavigate={closeMenu}
              />
            ))}
            {me?.isPlatformAdmin && (
              <>
                <span className="mx-1 hidden h-4 w-px bg-border-strong md:block" aria-hidden="true" />
                {ADMIN_NAV_ITEMS.map((item) => (
                  <NavLink
                    key={item.to}
                    item={item}
                    pathname={location.pathname}
                    activeClassName="bg-warning-muted text-warning"
                    onNavigate={closeMenu}
                  />
                ))}
              </>
            )}
          </nav>
          <div className="flex items-center gap-3 text-sm">
            <span className="hidden text-fg-muted sm:inline">{me?.email}</span>
            <CommandPalette isAdmin={Boolean(me?.isPlatformAdmin)} />
            <ThemeToggle />
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
              {t("legacy.appShell.t1")}</button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">{children}</main>
    </div>
  );
}
