import type { ButtonHTMLAttributes } from "react";
import { cn } from "./cn";
import { useTheme } from "./theme";

interface ThemeToggleProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onClick" | "children"> {
  /** 호출자가 로케일 문구를 넣을 수 있게 열어둔다 -- ui-web은 카탈로그를 모른다. */
  "aria-label"?: string;
}

// 아이콘만으로 상태를 표현한다(유니코드 글리프, 언어 무관) -- apps/web의
// i18n 리터럴 게이트(check_i18n_literals.mjs)는 한글만 잡으므로 이 컴포넌트
// 자체는 걸리지 않지만, 소비자가 aria-label을 한국어로 넘기면 그쪽 파일에서
// 걸릴 수 있다는 점은 호출자 책임이다.
const ICON: Record<"light" | "dark", string> = {
  dark: "☀", // ☀ -- 다크 모드일 때는 "라이트로 전환" 액션을 보여준다
  light: "☽", // ☾ -- 라이트 모드일 때는 "다크로 전환" 액션을 보여준다
};

export function ThemeToggle({ className, "aria-label": ariaLabel, ...rest }: ThemeToggleProps) {
  const { theme, toggleTheme } = useTheme();
  return (
    <button
      {...rest}
      type="button"
      onClick={toggleTheme}
      aria-label={ariaLabel ?? "Toggle color theme"}
      data-theme-toggle={theme}
      className={cn(
        "inline-flex h-8 w-8 items-center justify-center rounded-md border border-border-strong text-fg-secondary transition-colors hover:bg-surface-hover hover:text-fg",
        className,
      )}
    >
      <span aria-hidden="true">{ICON[theme]}</span>
    </button>
  );
}
