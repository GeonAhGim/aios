import { useSetupMfa, useVerifyMfa } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import QRCode from "qrcode";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

// FD-11.2 필수 게이트 — 정책문서 §4.10 "MFA는 사용자 레벨에서도 예외 없이
// 강제". 완료 전까지 ProtectedRoute가 다른 화면 진입을 막는다.
export function MfaSetupPage() {
  const setupMfa = useSetupMfa();
  const verifyMfa = useVerifyMfa();
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [totpCode, setTotpCode] = useState("");
  const [error, setError] = useState<string | null>(null);
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
    });
    // 최초 마운트 시 1회만 발급 요청.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await verifyMfa.mutateAsync(totpCode);
      navigate("/onboarding/risk-assessment");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "인증에 실패했습니다.");
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950 px-4">
      <div className="w-full max-w-sm space-y-4 text-slate-100">
        <h1 className="text-2xl font-semibold">2단계 인증 설정 (필수)</h1>
        <p className="text-sm text-slate-400">
          Google Authenticator 등 인증 앱으로 아래 QR코드를 스캔한 뒤, 앱에 표시된 6자리
          코드를 입력해주세요.
        </p>
        {qrDataUrl ? (
          <img src={qrDataUrl} alt="MFA QR 코드" className="mx-auto rounded bg-white p-2" />
        ) : (
          <p className="text-center text-slate-500">QR코드 생성 중...</p>
        )}
        {secret && (
          <p className="break-all text-center text-xs text-slate-500">
            수동 입력용 코드: {secret}
          </p>
        )}
        <form onSubmit={handleSubmit} className="space-y-3">
          <input
            type="text"
            inputMode="numeric"
            required
            placeholder="6자리 코드"
            value={totpCode}
            onChange={(e) => setTotpCode(e.target.value)}
            className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-center text-lg tracking-widest text-slate-100 outline-none focus:border-slate-400"
          />
          {error && <p className="text-sm text-red-400">{error}</p>}
          <button
            type="submit"
            disabled={verifyMfa.isPending || !qrDataUrl}
            className="w-full rounded bg-slate-100 px-3 py-2 font-medium text-slate-950 hover:bg-white disabled:opacity-50"
          >
            {verifyMfa.isPending ? "확인 중..." : "인증 완료"}
          </button>
        </form>
      </div>
    </div>
  );
}
