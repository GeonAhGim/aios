import type { ReactNode } from "react";

// L4_platform_observability_tenancy_api_v1.0.md §3.3: RESOURCE_NOT_FOUND(404)는 "재시도"
// 열이 "아니오"이므로 ErrorMessage(재시도 배너)가 아니라 이 "없음" 상태로 렌더한다.
// 순수 프레젠테이션 컴포넌트 — 재시도 버튼을 두지 않고 trace_id 등 내부 정보도 받지 않는다.
interface NotFoundStateProps {
  title: string;
  description?: string;
  action?: ReactNode;
}

export function NotFoundState({ title, description, action }: NotFoundStateProps) {
  return (
    <div className="rounded-lg border border-dashed border-border py-12 text-center">
      <p className="text-base font-medium text-fg">{title}</p>
      {description && <p className="mt-1 text-sm text-fg-muted">{description}</p>}
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  );
}
