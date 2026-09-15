import { Button, useDialogFocusTrap } from "@aios/ui-web";
import { useRef } from "react";

// FD-15.3 — 마켓플레이스 구매, 전략 배포 승인, ApprovalMode 변경 3곳에서
// 재사용하는 위험등급 불일치 경고 모달.
// UX-4 task-2688 — 포커스 트랩 + Esc 닫기(취소와 동일, 동의는 명시 클릭만) +
// 닫힌 뒤 트리거로 포커스 복귀. AlertFromChart.tsx(CH-9)와 같은 공용 훅(useDialogFocusTrap).
interface RiskWarningModalProps {
  reason: string;
  onConsent: () => void;
  onCancel: () => void;
  isPending?: boolean;
}

const TITLE_ID = "risk-warning-modal-title";

export function RiskWarningModal({
  reason,
  onConsent,
  onCancel,
  isPending,
}: RiskWarningModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialogFocusTrap({ isOpen: true, onClose: onCancel, containerRef: dialogRef });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={TITLE_ID}
        className="w-full max-w-md space-y-4 rounded-xl border border-warning/30 bg-surface p-6"
      >
        <div className="flex items-center gap-2 text-warning">
          <span aria-hidden>⚠</span>
          <h2 id={TITLE_ID} className="text-lg font-semibold">
            위험등급 불일치 경고
          </h2>
        </div>
        <p className="text-sm text-fg-secondary">{reason}</p>
        <p className="text-xs text-fg-muted">
          이는 강제 차단이 아니라 참고용 경고입니다. 계속 진행하려면 아래에서 동의해주세요.
        </p>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onCancel}>
            취소
          </Button>
          <Button
            type="button"
            onClick={onConsent}
            loading={isPending}
            className="!bg-warning !text-slate-950 hover:!bg-warning/90"
          >
            동의하고 계속
          </Button>
        </div>
      </div>
    </div>
  );
}
