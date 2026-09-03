import { useSetupMfa, useVerifyMfa } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyBadRequest, classifyForbidden, routeApiError } from "@aios/shared-types";
import { Button, Input } from "@aios/ui-web";
import QRCode from "qrcode";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { AuthLayout } from "../auth/AuthLayout";

// spec §3.3/§3.4: 설정·검증 실패는 err.message를 직접 노출하지 않고
// routeApiError(task-483)로 판정한다. 재발급(setup)은 이미 MFA가 켜진 계정을
// 비밀번호 없이 재호출하면 403 AUTH_MFA_REQUIRED로 거부되고(auth.py
// setup_mfa), 검증(verify) 코드가 틀리면 400 AUTH_MFA_INVALID로 거부된다 —
// 둘 다 classifyForbidden/classifyBadRequest(task-393/406)를 그대로
// 재사용한다(task-902).
function MfaError({ error }: { error: unknown }) {
  if (classifyBadRequest(error)) return <BadRequestNotice error={error} />;
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
    />
  );
}

// FD-11.2 필수 게이트 — 정책문서 §4.10 "MFA는 사용자 레벨에서도 예외 없이
// 강제". 완료 전까지 ProtectedRoute가 다른 화면 진입을 막는다.
export function MfaSetupPage() {
  const setupMfa = useSetupMfa();
  const verifyMfa = useVerifyMfa();
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [totpCode, setTotpCode] = useState("");
  const [setupError, setSetupError] = useState<unknown>(null);
  const [verifyError, setVerifyError] = useState<unknown>(null);
  const navigate = useNavigate();
  // React StrictMode(dev)가 마운트 이펙트를 두 번 실행한다 — 이 setup 호출은
  // 매번 서버의 mfa_secret을 덮어쓰므로(mfa_service.py::setup), 가드 없이
  // 두 번 부르면 화면에 보이는 QR코드와 실제 서버 상태가 어긋날 수 있다.
  const setupRequested = useRef(false);

  useEffect(() => {
    if (setupRequested.current) return;
    setupRequested.current = true;
    setupMfa.mutate(undefined, {
      onSuccess: async (result) => {
        setSecret(result.secret);
        setQrDataUrl(await QRCode.toDataURL(result.provisioningUri));
      },
      onError: (err) => {
        setSetupError(err instanceof ApiError ? err : new Error("설정 발급에 실패했습니다."));
      },
    });
    // 최초 마운트 시 1회만 발급 요청.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setVerifyError(null);
    try {
      await verifyMfa.mutateAsync(totpCode);
      navigate("/onboarding/risk-assessment");
    } catch (err) {
      setVerifyError(err instanceof ApiError ? err : new Error("인증에 실패했습니다."));
    }
  }

  return (
    <AuthLayout
      title="2단계 인증 설정 (필수)"
      subtitle="Google Authenticator 등 인증 앱으로 QR코드를 스캔해주세요"
    >
      <div className="space-y-4">
        {setupError !== null && <MfaError error={setupError} />}
        {qrDataUrl ? (
          <img src={qrDataUrl} alt="MFA QR 코드" className="mx-auto rounded-lg bg-white p-3" />
        ) : (
          <div className="flex h-40 items-center justify-center text-sm text-fg-muted">
            QR코드 생성 중...
          </div>
        )}
        {secret && (
          <p className="break-all rounded-md bg-surface-hover px-3 py-2 text-center font-mono text-xs text-fg-muted">
            수동 입력용 코드: {secret}
          </p>
        )}
        <form onSubmit={handleSubmit} className="space-y-3">
          <Input
            type="text"
            inputMode="numeric"
            required
            placeholder="6자리 코드"
            value={totpCode}
            onChange={(e) => setTotpCode(e.target.value)}
            className="text-center text-lg tracking-[0.3em]"
          />
          {verifyError !== null && <MfaError error={verifyError} />}
          <Button type="submit" loading={verifyMfa.isPending} disabled={!qrDataUrl} className="w-full">
            인증 완료
          </Button>
        </form>
      </div>
    </AuthLayout>
  );
}
