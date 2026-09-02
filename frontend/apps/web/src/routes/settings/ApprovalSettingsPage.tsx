import { useApprovalSettings, useUpdateApprovalSettings } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { Alert, Button, Field, Input, LoadingState, PageHeader } from "@aios/ui-web";
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
        <PageHeader title="승인 방식 설정" />
        {isLoading ? (
          <LoadingState />
        ) : (
          <div className="space-y-4 rounded-lg border border-border bg-surface p-6">
            <div className="space-y-2">
              <label className="flex items-center gap-2 text-sm text-fg">
                <input
                  type="radio"
                  checked={mode === "SOLO"}
                  onChange={() => setMode("SOLO")}
                  className="accent-accent"
                />
                SOLO — 본인 1인 승인(강제 대기 60초)
              </label>
              <label className="flex items-center gap-2 text-sm text-fg">
                <input
                  type="radio"
                  checked={mode === "DUAL"}
                  onChange={() => setMode("DUAL")}
                  className="accent-accent"
                />
                DUAL — 서로 다른 두 계정의 순차 서명
              </label>
            </div>
            {mode === "DUAL" && (
              <Field label="2차 승인자 연락처">
                <Input
                  type="text"
                  value={secondApproverContact}
                  onChange={(e) => setSecondApproverContact(e.target.value)}
                />
              </Field>
            )}
            {settings && (
              <p className="text-sm text-fg-muted">
                현재 강제 대기시간: {settings.mandatoryWaitSeconds}초
              </p>
            )}
            {error && <Alert>{error}</Alert>}
            <Button type="button" onClick={() => attemptUpdate(false)} loading={update.isPending}>
              저장
            </Button>
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
