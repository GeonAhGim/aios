import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "./cn";

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      {...rest}
      className={cn("rounded-lg border border-border bg-surface p-6", className)}
    />
  );
}

export function CardTitle({ className, ...rest }: HTMLAttributes<HTMLHeadingElement>) {
  return <h2 {...rest} className={cn("mb-4 text-lg font-semibold text-fg", className)} />;
}

interface StatProps {
  label: string;
  value: ReactNode;
  tone?: "default" | "success" | "danger";
}

export function Stat({ label, value, tone = "default" }: StatProps) {
  const toneClass =
    tone === "success" ? "text-success" : tone === "danger" ? "text-danger" : "text-fg";
  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <p className="text-xs text-fg-muted">{label}</p>
      <p className={cn("tabular mt-1 text-xl font-semibold", toneClass)}>{value}</p>
    </div>
  );
}
