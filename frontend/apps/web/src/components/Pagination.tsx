import { Button } from "@aios/ui-web";
import type { PageState } from "../lib/pagination";

// derivePageState(§9 PLT-12)의 출력을 받아 이전/다음 버튼과 현재 페이지만
// 그리는 표시 전용 컴포넌트. 클램프·비활성 조건은 derivePageState가 이미
// 계산해 두므로 여기서는 그 값을 그대로 반영한다.
interface PaginationProps {
  state: PageState;
  onPageChange: (page: number) => void;
}

export function Pagination({ state, onPageChange }: PaginationProps) {
  if (state.totalPages !== null && state.totalPages <= 1) {
    return null;
  }

  return (
    <div className="flex items-center justify-center gap-2">
      <Button
        type="button"
        variant="secondary"
        size="sm"
        disabled={!state.hasPrev}
        onClick={() => onPageChange(state.page - 1)}
      >
        이전
      </Button>
      <span className="tabular flex items-center px-2 text-sm text-fg-muted">
        {state.totalPages !== null
          ? `${state.page} / ${state.totalPages}`
          : state.page}
      </span>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        disabled={!state.hasNext}
        onClick={() => onPageChange(state.page + 1)}
      >
        다음
      </Button>
    </div>
  );
}
