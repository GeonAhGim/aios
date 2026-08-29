import type { ReactNode } from "react";
import { cn } from "./cn";

export function Alert({
  tone = "danger",
  children,
}: {
  tone?: "danger" | "success" | "warning";
  children: ReactNode;
}) {
  const toneClass =
    tone === "success"
      ? "border-success/30 bg-success-muted text-success"
      : tone === "warning"
        ? "border-warning/30 bg-warning-muted text-warning"
        : "border-danger/30 bg-danger-muted text-danger";
  return (
    <div className={cn("rounded-md border px-4 py-3 text-sm", toneClass)}>{children}</div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-border py-12 text-center text-sm text-fg-muted">
      {children}
    </div>
  );
}

export function LoadingState() {
  return (
    <div className="flex items-center gap-2 py-8 text-sm text-fg-muted">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-fg-muted border-t-transparent" />
      불러오는 중...
    </div>
  );
}

export function PageHeader({ title, action }: { title: string; action?: ReactNode }) {
  return (
    <div className="flex items-center justify-between">
      <h1 className="text-2xl font-semibold tracking-tight text-fg">{title}</h1>
      {action}
    </div>
  );
}
