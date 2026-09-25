import { configureMfaStepUpHandler } from "@aios/api-client";
import { useVerifyMfa } from "@aios/shared-hooks";
import { Alert, Button, Input, useDialogFocusTrap } from "@aios/ui-web";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

// spec §3.3/§3.4: http.ts가 403 AUTH_MFA_REQUIRED를 받으면 mfaStepUp.ts의
// requestMfaStepUp()을 거쳐 이 컴포넌트가 등록한 핸들러를 부른다(task-481).
// 앱 루트에 1곳만 마운트해 configureMfaStepUpHandler로 핸들러를 등록하고,
// 다이얼로그를 띄워 사용자의 TOTP 재인증을 기다린다 — 성공하면 true를
// 반환해 원요청이 1회 재시도되고, 취소하거나 AUTH_MFA_INVALID면 false를
// 반환해 원래의 403 ApiError가 그대로 전파된다(decision).
//
// 새 엔드포인트를 만들지 않고 기존 POST /auth/mfa/verify(useVerifyMfa →
// authClient.verifyMfa)만 호출한다.
const TITLE_ID = "mfa-step-up-dialog-title";

export function MfaStepUpDialog() {
  const { t } = useTranslation();
  const verifyMfa = useVerifyMfa();
  const [isOpen, setIsOpen] = useState(false);
  const [totpCode, setTotpCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const resolveRef = useRef<((ok: boolean) => void) | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  // 성공·실패·언마운트 모든 경로에서 TOTP 입력값을 즉시 비운다(decision 4번).
  function finish(ok: boolean) {
    setIsOpen(false);
    setTotpCode("");
    resolveRef.current?.(ok);
    resolveRef.current = null;
  }

  useEffect(() => {
    configureMfaStepUpHandler(
      () =>
        new Promise<boolean>((resolve) => {
          resolveRef.current = resolve;
          setError(null);
          setIsOpen(true);
        }),
    );
    return () => {
      configureMfaStepUpHandler(null);
      setTotpCode("");
      resolveRef.current?.(false);
      resolveRef.current = null;
    };
    // 앱 부트스트랩 시 1회만 핸들러를 등록한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await verifyMfa.mutateAsync(totpCode);
      finish(true);
    } catch {
      setTotpCode("");
      setError(t("legacy.mfaStepUpDialog.t6"));
    }
  }

  // UX-4 task-2688: 포커스 트랩 + Esc 닫기(취소와 동일) + 닫힌 뒤 트리거로
  // 포커스 복귀. AlertFromChart.tsx(CH-9)와 같은 공용 훅.
  useDialogFocusTrap({ isOpen, onClose: () => finish(false), containerRef: dialogRef });

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={TITLE_ID}
        className="w-full max-w-sm space-y-4 rounded-xl border border-border bg-surface p-6"
      >
        <h2 id={TITLE_ID} className="text-lg font-semibold text-fg">
          {t("legacy.mfaStepUpDialog.t1")}
        </h2>
        <p className="text-sm text-fg-secondary">
          {t("legacy.mfaStepUpDialog.t2")}</p>
        <form onSubmit={handleSubmit} className="space-y-3">
          <Input
            type="text"
            inputMode="numeric"
            required
            autoFocus
            placeholder={t("legacy.mfaStepUpDialog.placeholder3")}
            value={totpCode}
            onChange={(e) => setTotpCode(e.target.value)}
            className="text-center text-lg tracking-[0.3em]"
          />
          {error && <Alert>{error}</Alert>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => finish(false)}>
              {t("legacy.mfaStepUpDialog.t4")}</Button>
            <Button type="submit" loading={verifyMfa.isPending}>
              {t("legacy.mfaStepUpDialog.t5")}</Button>
          </div>
        </form>
      </div>
    </div>
  );
}
