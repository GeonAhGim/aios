import { Children, cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from "react";

interface FieldProps {
  label: string;
  htmlFor?: string;
  hint?: string;
  error?: string | null;
  children: ReactNode;
}

/**
 * `htmlFor`가 없고 자식이 단일 요소면 `useId()`로 만든 id를 그 요소에
 * 주입해 label과 input을 프로그래밍적으로 연결한다(UX-4 task-2688 -- 이전엔
 * 시각적으로만 붙어 있고 `for`/`id` 연결이 없어 스크린리더가 label 텍스트를
 * 입력의 접근 가능한 이름으로 읽지 않았다). 자식이 이미 id를 갖고 있거나
 * 단일 요소가 아니면 건드리지 않는다.
 */
export function Field({ label, htmlFor, hint, error, children }: FieldProps) {
  const generatedId = useId();
  const child =
    Children.count(children) === 1 && isValidElement(children)
      ? (children as ReactElement<{ id?: string }>)
      : null;
  const inputId = htmlFor ?? child?.props.id ?? generatedId;
  const associatedChild = child && !child.props.id ? cloneElement(child, { id: inputId }) : children;

  return (
    <div className="space-y-1.5">
      <label htmlFor={inputId} className="text-sm font-medium text-fg-secondary">
        {label}
      </label>
      {associatedChild}
      {hint && !error && <p className="text-xs text-fg-muted">{hint}</p>}
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  );
}
