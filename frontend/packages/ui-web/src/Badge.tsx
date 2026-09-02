import type { HTMLAttributes } from "react";
import { cn } from "./cn";

type Tone = "neutral" | "success" | "danger" | "warning" | "accent";

const TONE_CLASSES: Record<Tone, string> = {
  neutral: "bg-surface-hover text-fg-secondary",
  success: "bg-success-muted text-success",
  danger: "bg-danger-muted text-danger",
  warning: "bg-warning-muted text-warning",
  accent: "bg-accent-muted text-accent-hover",
};

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
}

export function Badge({ tone = "neutral", className, ...rest }: BadgeProps) {
  return (
    <span
      {...rest}
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        TONE_CLASSES[tone],
        className,
      )}
    />
  );
}
