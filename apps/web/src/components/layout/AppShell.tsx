import { useLogout, useMe } from "@aios/shared-hooks";
import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

const NAV_ITEMS = [
  { to: "/dashboard", label: "대시보드" },
  { to: "/executions", label: "실행 제어판" },
  { to: "/portfolio", label: "포트폴리오" },
];

export function AppShell({ children }: { children: ReactNode }) {
  const { data: me } = useMe();
  const logout = useLogout();
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 px-6 py-4">
        <div className="mx-auto flex max-w-6xl items-center justify-between">
          <Link to="/dashboard" className="text-lg font-semibold tracking-tight">
            AIOS
          </Link>
          <nav className="flex items-center gap-6 text-sm text-slate-300">
            {NAV_ITEMS.map((item) => (
              <Link key={item.to} to={item.to} className="hover:text-white">
                {item.label}
              </Link>
            ))}
          </nav>
          <div className="flex items-center gap-4 text-sm text-slate-400">
            <span>{me?.email}</span>
            <button
              type="button"
              onClick={() => {
                logout();
                navigate("/login");
              }}
              className="rounded border border-slate-700 px-3 py-1 hover:bg-slate-800"
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
