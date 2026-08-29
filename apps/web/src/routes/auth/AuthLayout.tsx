import type { ReactNode } from "react";

export function AuthLayout({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-3">
          <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-accent text-lg font-bold text-white">
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
