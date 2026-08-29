import { useApprovalSettings, useUpdateApprovalSettings } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { useEffect, useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { RiskWarningModal } from "../../components/RiskWarningModal";

export function ApprovalSettingsPage() {
  const { data: settings, isLoading } = useApprovalSettings();
  const update = useUpdateApprovalSettings();
  const [mode, setMode] = useState<"SOLO" | "DUAL">("SOLO");
  const [secondApproverContact, setSecondApproverContact] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [riskWarningReason, setRiskWarningReason] = useState<string | null>(null);

  useEffect(() => {
    if (settings) setMode(settings.mode);
  }, [settings]);

  async function attemptUpdate(acknowledged: boolean) {
    setError(null);
    try {
      await update.mutateAsync({
        mode,
        secondApproverContact: mode === "DUAL" ? secondApproverContact : undefined,
        riskWarningAcknowledged: acknowledged,
      });
      setRiskWarningReason(null);
    } catch (err) {
      if (err instanceof ApiError) {
        if (!acknowledged && err.message.includes("위험등급")) {
          setRiskWarningReason(err.message);
          return;
        }
        setError(err.message);
      } else {
        setError("설정 변경에 실패했습니다.");
      }
    }
  }

  return (
    <AppShell>
      <div className="max-w-md space-y-6">
        <h1 className="text-2xl font-semibold text-slate-100">승인 방식 설정</h1>
        {isLoading ? (
          <p className="text-slate-500">불러오는 중...</p>
        ) : (
          <div className="space-y-4">
            <div className="space-y-2">
              <label className="flex items-center gap-2 text-slate-200">
                <input
                  type="radio"
                  checked={mode === "SOLO"}
                  onChange={() => setMode("SOLO")}
                />
                SOLO — 본인 1인 승인(강제 대기 60초)
              </label>
              <label className="flex items-center gap-2 text-slate-200">
                <input
                  type="radio"
                  checked={mode === "DUAL"}
                  onChange={() => setMode("DUAL")}
                />
                DUAL — 서로 다른 두 계정의 순차 서명
              </label>
            </div>
            {mode === "DUAL" && (
              <div className="space-y-1">
                <label className="text-sm text-slate-400">2차 승인자 연락처</label>
                <input
                  type="text"
                  value={secondApproverContact}
                  onChange={(e) => setSecondApproverContact(e.target.value)}
                  className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
                />
              </div>
            )}
            {settings && (
              <p className="text-sm text-slate-500">
                현재 강제 대기시간: {settings.mandatoryWaitSeconds}초
              </p>
            )}
            {error && <p className="text-sm text-red-400">{error}</p>}
            <button
              type="button"
              onClick={() => attemptUpdate(false)}
              disabled={update.isPending}
              className="rounded bg-slate-100 px-4 py-2 text-sm font-medium text-slate-950 hover:bg-white disabled:opacity-50"
            >
              {update.isPending ? "저장 중..." : "저장"}
            </button>
          </div>
        )}
      </div>

      {riskWarningReason && (
        <RiskWarningModal
          reason={riskWarningReason}
          isPending={update.isPending}
          onConsent={() => attemptUpdate(true)}
          onCancel={() => setRiskWarningReason(null)}
        />
      )}
    </AppShell>
  );
}
