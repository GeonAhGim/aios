// FD-15.3 — 마켓플레이스 구매, 전략 배포 승인, ApprovalMode 변경 3곳에서
// 재사용하는 위험등급 불일치 경고 모달.
interface RiskWarningModalProps {
  reason: string;
  onConsent: () => void;
  onCancel: () => void;
  isPending?: boolean;
}

export function RiskWarningModal({
  reason,
  onConsent,
  onCancel,
  isPending,
}: RiskWarningModalProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
      <div className="w-full max-w-md space-y-4 rounded-lg border border-amber-800 bg-slate-900 p-6">
        <div className="flex items-center gap-2 text-amber-400">
          <span aria-hidden>⚠</span>
          <h2 className="text-lg font-semibold">위험등급 불일치 경고</h2>
        </div>
        <p className="text-sm text-slate-300">{reason}</p>
        <p className="text-xs text-slate-500">
          이는 강제 차단이 아니라 참고용 경고입니다. 계속 진행하려면 아래에서 동의해주세요.
        </p>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800"
          >
            취소
          </button>
          <button
            type="button"
            onClick={onConsent}
            disabled={isPending}
            className="rounded bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-500 disabled:opacity-50"
          >
            {isPending ? "처리 중..." : "동의하고 계속"}
          </button>
        </div>
      </div>
    </div>
  );
}
